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
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = self._context.new_page()
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
        page = self._page
        log.info(f"Acessando o portal CORBAN como '{self._credencial_ativa.usuario}'…")
        page.goto(self.config.site_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)

        log.info("Preenchendo credenciais…")
        page.fill("#exten", self._credencial_ativa.usuario)
        page.fill("#password", self._credencial_ativa.senha)
        page.click("#button-sigin")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
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
        page = self._page
        seletor_input = "#telefoneBuscaTelefoneConsultaRapida"
        try:
            inp = page.locator(seletor_input)
            if inp.is_visible():
                return
        except Exception:
            pass

        # Clica no botão com ícone de telefone
        seletor_botao = (
            "button[onclick*='buscarPorIdRapida'], "
            "button[data-original-title*='telefone'], "
            "button:has(i.fa-phone)"
        )
        try:
            btn = page.locator(seletor_botao).first
            if btn.is_visible():
                btn.click()
            else:
                page.evaluate("if (typeof buscarPorIdRapida === 'function') buscarPorIdRapida();")
        except Exception:
            page.evaluate("if (typeof buscarPorIdRapida === 'function') buscarPorIdRapida();")

        page.wait_for_selector(seletor_input, state="visible", timeout=8_000)

    def buscar_cpfs_por_telefone(self, telefone_raw: str) -> list[str]:
        """
        Pesquisa o telefone no Corban e extrai o(s) CPF(s) encontrados.
        Retorna lista de CPFs com formatação 000.000.000-00.
        Lança LimiteConsultasExcedido se o portal acusar limite diário ou atingir timeout.
        """
        page = self._page
        tel_limpo = limpar_telefone(telefone_raw)

        try:
            self._abrir_modal_busca_telefone()

            campo = page.locator("#telefoneBuscaTelefoneConsultaRapida")
            campo.click()
            campo.fill("")
            campo.type(tel_limpo, delay=40)
            page.wait_for_timeout(300)

            page.click("#btnBuscarcpfPorCliente")

            tempo_inicio = time.time()
            cpfs_encontrados: list[str] = []
            resultado_obtido = False

            while time.time() - tempo_inicio < self.config.timeout_resposta:
                resultado = page.evaluate("""() => {
                    const linhas = Array.from(document.querySelectorAll('tr')).filter(tr => {
                        const links = tr.querySelectorAll('a[onclick*="levarcodigoRapido2"], a.btn-primary');
                        return links.length > 0;
                    });

                    if (linhas.length === 0) {
                        const alertas = Array.from(
                            document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body, .swal2-title')
                        ).map(el => el.innerText.trim());
                        return { status: 'aguardando', linhas: [], alertas: alertas };
                    }

                    // Determina a coluna do CPF
                    let colCpfIndex = 0;
                    const ths = Array.from(document.querySelectorAll('th')).map(th => th.innerText.toLowerCase().trim());
                    const idxTh = ths.findIndex(t => t.includes('cpf'));
                    if (idxTh !== -1) {
                        colCpfIndex = idxTh;
                    }

                    const dados = [];
                    for (const tr of linhas) {
                        const tds = tr.querySelectorAll('td');
                        if (tds.length >= 2) {
                            const txt = tds[colCpfIndex] ? tds[colCpfIndex].innerText.trim() : '';
                            if (txt) dados.push(txt);
                        }
                    }

                    return { status: 'concluido', cpfs: dados, alertas: [] };
                }""")

                alertas = resultado.get("alertas", [])

                # 1. Verifica se houve alerta explícito de limite diário atingido
                if any("limite" in a.lower() for a in alertas):
                    log.warning(f"  [ALERTA DETECTADO] Limite diário informado pelo sistema: {alertas}")
                    raise LimiteConsultasExcedido(
                        f"Limite diário atingido na conta '{self._credencial_ativa.usuario}'."
                    )

                # 2. Verifica se houve sucesso com linhas de CPF retornadas
                if resultado.get("status") == "concluido" and resultado.get("cpfs"):
                    for raw_cpf in resultado["cpfs"]:
                        c_limpo = limpar_cpf(raw_cpf)
                        if len(c_limpo) == 11:
                            cpf_fmt = formatar_cpf(c_limpo)
                            if cpf_fmt not in cpfs_encontrados:
                                cpfs_encontrados.append(cpf_fmt)
                    resultado_obtido = True
                    break

                # 3. Verifica se houve aviso de nenhum cliente retornado
                if any("nenhum" in a.lower() or "não encontrado" in a.lower() for a in alertas):
                    log.info("  Aviso: Nenhum cliente retornado para este telefone.")
                    resultado_obtido = True
                    break

                page.wait_for_timeout(600)

            # Se excedeu o tempo sem resposta e sem aviso de nenhum cliente, indica limite de requisições
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
