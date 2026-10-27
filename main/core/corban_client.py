import logging
import time
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from core.config import AppConfig
from core.domain import (
    Credencial,
    ErroParadaImediata,
    LimiteConsultasExcedido,
    formatar_cpf,
    limpar_cpf,
    limpar_telefone,
)

log = logging.getLogger(__name__)


class CorbanClient:
    """
    Adapter de automação do portal Sistema Corban.
    Encapsula o ciclo de vida do Playwright, autenticação, alternância de contas
    e raspagem do modal de pesquisa por telefone.
    """

    def __init__(self, config: AppConfig, credencial_inicial: Optional[Credencial] = None):
        self.config = config
        self._credencial_ativa = (
            credencial_inicial
            if credencial_inicial
            else (config.contas[0] if config.contas else Credencial("", ""))
        )
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def credencial_ativa(self) -> Credencial:
        """Retorna a credencial atualmente em uso."""
        return self._credencial_ativa

    @property
    def page(self) -> Page:
        """Retorna a página ativa do navegador ou lança exceção se não estiver inicializada."""
        if self._page is None:
            raise RuntimeError("Página do navegador não inicializada. Chame iniciar() antes.")
        return self._page

    @property
    def browser(self) -> Browser:
        """Retorna a instância do navegador ou lança exceção se não estiver inicializada."""
        if self._browser is None:
            raise RuntimeError("Navegador não inicializado. Chame iniciar() antes.")
        return self._browser

    def __enter__(self) -> "CorbanClient":
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.fechar()

    def iniciar(self) -> None:
        """Inicia o navegador, cria a página e realiza o login inicial."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.config.modo_visivel,
            slow_mo=80,
        )
        self._criar_contexto_e_logar()

    def _criar_contexto_e_logar(self) -> None:
        """Cria um novo contexto isolado de navegação e efetua login."""
        self._context = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = self._context.new_page()

        # Aceita automaticamente diálogos do navegador, como confirmação de sessão já aberta
        self._page.on("dialog", lambda dialog: dialog.accept())

        self._fazer_login()

    def alternar_conta(self, nova_credencial: Credencial) -> None:
        """
        Alterna a conta ativa fechando o contexto anterior e efetuando um login limpo.
        Útil para contornar limites diários de consultas.
        """
        log.info(f"🔄 Alternando conta de '{self._credencial_ativa.usuario}' para '{nova_credencial.usuario}'...")
        try:
            if self._context:
                self._context.close()
        except Exception as e:
            log.debug(f"Aviso ao fechar contexto anterior: {e}")

        self._credencial_ativa = nova_credencial
        self._criar_contexto_e_logar()
        log.info(f"✔ Sessão iniciada com sucesso na conta '{nova_credencial.usuario}'.")

    def fechar(self) -> None:
        """Encerra a sessão do navegador e limpa todos os recursos."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            log.debug(f"Aviso ao fechar navegador: {e}")

    def _fazer_login(self) -> None:
        """Realiza autenticação no portal Corban com a credencial ativa."""
        page = self.page
        log.info(f"Acessando o portal CORBAN como '{self._credencial_ativa.usuario}'…")
        page.goto(self.config.site_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)

        log.info("Preenchendo credenciais…")
        page.fill("#exten", self._credencial_ativa.usuario)
        page.fill("#password", self._credencial_ativa.senha)
        page.click("#button-sigin")

        # Aguarda a transição de navegação do login para o painel principal
        try:
            page.wait_for_url("**/index.php/**", timeout=20_000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2_000)

        if page.query_selector("#button-sigin") or "login" in page.url.lower():
            raise RuntimeError(
                f"Falha no login com o usuário '{self._credencial_ativa.usuario}'! "
                f"Verifique as credenciais no arquivo .env."
            )

        log.info(f"✔ Login realizado com sucesso como '{self._credencial_ativa.usuario}'.")

    def _abrir_modal_busca_telefone(self) -> None:
        """Garante que a caixa de pesquisa de telefone esteja aberta e visível."""
        page = self.page
        seletor_input = "#telefoneBuscaTelefoneConsultaRapida"
        try:
            inp = page.locator(seletor_input)
            if inp.is_visible():
                return
        except Exception:
            pass

        # Clica no botão com ícone de telefone ou abre via JavaScript/Bootstrap
        seletor_botao = (
            "button[onclick*='buscarPorIdRapida'], "
            "button[data-original-title*='telefone'], "
            "button:has(i.fa-phone)"
        )

        for tentativa in range(1, 4):
            try:
                btn = page.locator(seletor_botao).first
                if btn.is_visible():
                    btn.click()
                else:
                    page.evaluate("""() => {
                        if (typeof buscarPorIdRapida === 'function') {
                            buscarPorIdRapida();
                        } else if (typeof $ !== 'undefined' && $('#modal-buscaid-rapida').length) {
                            $('#modal-buscaid-rapida').modal('show');
                        }
                    }""")
            except Exception as e:
                log.debug(f"Aviso na tentativa {tentativa} de abrir modal: {e}")

            try:
                page.wait_for_selector(seletor_input, state="visible", timeout=4_000)
                return
            except PlaywrightTimeout:
                log.warning(f"Modal não abriu na tentativa {tentativa}/3. Forçando via Bootstrap/jQuery...")
                try:
                    page.evaluate("""() => {
                        if (typeof $ !== 'undefined') {
                            $('#modal-buscaid-rapida').modal('show');
                        }
                    }""")
                except Exception:
                    pass
                page.wait_for_timeout(1_000)

        page.wait_for_selector(seletor_input, state="visible", timeout=5_000)

    def buscar_cpfs_por_telefone(self, telefone_raw: str) -> list[str]:
        """
        Pesquisa o telefone no Corban e extrai o(s) CPF(s) encontrados.
        Retorna lista de CPFs com formatação 000.000.000-00.
        Se aparecer 'Nenhum cliente encontrado', encerra imediatamente sem lançar erro de limite.
        Lança LimiteConsultasExcedido se o portal acusar limite diário explícito ou tempo limite sem resposta.
        """
        page = self.page
        tel_limpo = limpar_telefone(telefone_raw)

        try:
            self._abrir_modal_busca_telefone()

            campo = page.locator("#telefoneBuscaTelefoneConsultaRapida")
            campo.click()
            campo.fill("")
            campo.type(tel_limpo, delay=40)
            page.wait_for_timeout(300)

            # Limpa resultados ou avisos anteriores do modal antes de disparar a nova busca
            try:
                page.evaluate("""() => {
                    const btn = document.querySelector('#btnBuscarcpfPorCliente');
                    const modal = btn ? btn.closest('.modal, .modal-content, .modal-body, div') : null;
                    if (modal) {
                        modal.querySelectorAll('tr').forEach(tr => {
                            if (tr.querySelector('td')) tr.remove();
                        });
                    }
                }""")
            except Exception:
                pass

            page.click("#btnBuscarcpfPorCliente")
            page.wait_for_timeout(800)

            tempo_inicio = time.time()
            cpfs_encontrados: list[str] = []
            resultado_obtido = False

            while time.time() - tempo_inicio < self.config.timeout_resposta:
                resultado = page.evaluate("""() => {
                    // 1. Verifica se apareceu a célula indicando que nenhum cliente foi encontrado
                    // Ex: <td colspan="3" style="text-align: center; height: 25px;">Nenhum cliente encontrado</td>
                    const tds = Array.from(document.querySelectorAll('td'));
                    const tdNenhum = tds.find(td => {
                        const txt = (td.innerText || '').toLowerCase().trim();
                        return txt.includes('nenhum cliente encontrado') || 
                               txt.includes('nenhum registro encontrado') ||
                               txt.includes('cliente não encontrado');
                    });

                    if (tdNenhum) {
                        return { status: 'nao_encontrado', cpfs: [], alertas: [] };
                    }

                    // 2. Procura linhas que contenham resultados de clientes com links de ação
                    const linhas = Array.from(document.querySelectorAll('tr')).filter(tr => {
                        const links = tr.querySelectorAll('a[onclick*="levarcodigoRapido2"], a.btn-primary');
                        return links.length > 0;
                    });

                    if (linhas.length > 0) {
                        let colCpfIndex = 0;
                        const ths = Array.from(document.querySelectorAll('th')).map(th => th.innerText.toLowerCase().trim());
                        const idxTh = ths.findIndex(t => t.includes('cpf'));
                        if (idxTh !== -1) {
                            colCpfIndex = idxTh;
                        }

                        const dados = [];
                        for (const tr of linhas) {
                            const tdsRow = tr.querySelectorAll('td');
                            if (tdsRow.length >= 2) {
                                const txt = tdsRow[colCpfIndex] ? tdsRow[colCpfIndex].innerText.trim() : '';
                                if (txt && !txt.toLowerCase().includes('nenhum')) {
                                    dados.push(txt);
                                }
                            }
                        }

                        if (dados.length > 0) {
                            return { status: 'concluido', cpfs: dados, alertas: [] };
                        }
                    }

                    // 3. Verifica alertas modais (swal, toast, alert)
                    const alertas = Array.from(
                        document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body, .swal2-title')
                    ).map(el => el.innerText.trim());

                    if (alertas.some(a => a.toLowerCase().includes('nenhum') || a.toLowerCase().includes('não encontrado'))) {
                        return { status: 'nao_encontrado', cpfs: [], alertas: alertas };
                    }

                    return { status: 'aguardando', linhas: [], alertas: alertas };
                }""")

                alertas = resultado.get("alertas", [])

                # Alerta explícito de limite diário de consultas atingido
                if any("limite" in a.lower() for a in alertas):
                    log.warning(f"  [ALERTA DETECTADO] Limite diário informado pelo sistema: {alertas}")
                    raise LimiteConsultasExcedido(
                        f"Limite diário atingido na conta '{self._credencial_ativa.usuario}'."
                    )

                # Cliente não encontrado (td: "Nenhum cliente encontrado") -> Retorna lista vazia imediatamente
                if resultado.get("status") == "nao_encontrado":
                    log.info("  [-] Nenhum cliente encontrado no sistema para este número.")
                    resultado_obtido = True
                    break

                # Sucesso com CPF(s) retornados na tabela
                if resultado.get("status") == "concluido" and resultado.get("cpfs"):
                    for raw_cpf in resultado["cpfs"]:
                        c_limpo = limpar_cpf(raw_cpf)
                        if len(c_limpo) == 11:
                            cpf_fmt = formatar_cpf(c_limpo)
                            if cpf_fmt not in cpfs_encontrados:
                                cpfs_encontrados.append(cpf_fmt)
                    resultado_obtido = True
                    break

                page.wait_for_timeout(600)

            # Se excedeu o tempo sem resposta e sem a mensagem de nenhum cliente, indica lentidão ou limite do servidor
            if not resultado_obtido:
                raise LimiteConsultasExcedido(
                    f"Tempo limite de {self.config.timeout_resposta}s excedido aguardando resposta para o telefone {telefone_raw}. "
                    f"Provável limite diário atingido na conta '{self._credencial_ativa.usuario}'."
                )

            return cpfs_encontrados

        except LimiteConsultasExcedido:
            raise
        except Exception as e:
            msg_e = str(e)
            if "locator.click" in msg_e.lower() or isinstance(e, PlaywrightTimeout):
                raise ErroParadaImediata(f"Erro de Locator/Timeout no telefone {telefone_raw}: {e}")
            log.error(f"  Erro ao consultar telefone {telefone_raw}: {e}")
            raise ErroParadaImediata(f"Erro inesperado no telefone {telefone_raw}: {e}")
