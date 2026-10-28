import logging
from typing import List, Optional

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
    ResultadoClienteFiltro,
    calcular_idade,
    formatar_cpf,
    formatar_telefone,
    limpar_cpf,
)

log = logging.getLogger(__name__)


class CorbanClient:
    """
    Adapter de automação web para o portal Sistema Corban.
    Encapsula o ciclo de vida do Playwright, autenticação, rotação de contas,
    pesquisa de CPFs e aplicação dos filtros de idade, empréstimo e telefones.
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
        """Inicia o navegador Chromium e realiza a autenticação inicial."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.config.modo_visivel,
            slow_mo=80,
        )
        self._criar_contexto_e_logar()

    def _criar_contexto_e_logar(self) -> None:
        """Cria um contexto isolado no navegador e efetua o login no portal."""
        self._context = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = self._context.new_page()

        # Aceita automaticamente diálogos do navegador, como confirmação de sessão já aberta
        self._page.on("dialog", lambda dialog: dialog.accept())

        self._fazer_login()

    def alternar_conta(self, nova_credencial: Credencial) -> None:
        """Alterna a conta ativa fechando o contexto anterior e efetuando um login limpo."""
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
        """Encerra a sessão do navegador e libera os recursos alocados."""
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

    def _buscar_cpf(self, cpf_limpo: str) -> None:
        """Garante a seleção do tipo CPF e dispara a busca rápida."""
        page = self.page
        try:
            page.select_option("#typeOfDataSent", value="cpf")
            page.wait_for_timeout(300)
        except Exception:
            pass

        campo = page.locator("#dataToReceptivo")
        campo.click()
        campo.fill("")
        campo.type(cpf_limpo, delay=40)
        page.wait_for_timeout(300)

        page.click("#btnConsultaClienteRapido")
        page.wait_for_timeout(500)

    def _desanonimizar_telefones(self) -> None:
        """Clica no botão de desanonimização de telefones para exibir os números reais."""
        page = self.page
        try:
            btn = page.query_selector("#btnHideOrShowTelefone")
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(800)
            else:
                page.evaluate("""() => {
                    if (typeof abrirCamposTelefones === 'function') {
                        abrirCamposTelefones();
                    }
                }""")
                page.wait_for_timeout(500)
        except Exception as e:
            log.debug(f"Aviso ao liberar telefones: {e}")

    def _extrair_e_filtrar_beneficio(self, cpf_fmt: str) -> Optional[ResultadoClienteFiltro]:
        """
        Extrai as informações da página do benefício e aplica os filtros:
        1. Idade (< idade_limite)
        2. Parcelas pagas no último empréstimo (<= parcelas_pagas_limite)
        3. Telefones 1 e 2
        """
        page = self.page

        # ── 1. Extração da data/idade ──────────────────────────────
        nasc_raw = ""
        try:
            nasc_el = page.query_selector("#cliNascimento")
            if nasc_el:
                nasc_raw = nasc_el.inner_text().strip()
        except Exception as e:
            log.debug(f"Erro ao buscar #cliNascimento: {e}")

        idade = calcular_idade(nasc_raw)

        if idade is None:
            log.warning(f"     ⚠ Não foi possível determinar a idade (lido: '{nasc_raw}'). Pulando benefício.")
            return None

        if idade >= self.config.idade_limite:
            log.info(f"     [Filtro Reprovado] Idade {idade} anos >= limite de {self.config.idade_limite} anos. Pulando.")
            return None

        log.info(f"     [Filtro OK] Idade: {idade} anos (< {self.config.idade_limite})")

        # ── 2. Extração das parcelas pagas no último empréstimo ────
        pagas = None
        try:
            res_pagas = page.evaluate("""() => {
                const inp = document.querySelector('#linhaBanco--pagas_0');
                if (inp && inp.value !== '') {
                    const num = parseInt(inp.value.trim(), 10);
                    if (!isNaN(num)) return num;
                }

                const linha0 = document.querySelector('#titlelinha_0') || document.querySelector('.portlet-banco');
                if (linha0) {
                    const txt = linha0.innerText || '';
                    const m = /(\\d+)\\s*pagas/i.exec(txt);
                    if (m) return parseInt(m[1], 10);
                }

                return null;
            }""")
            if res_pagas is not None:
                pagas = int(res_pagas)
        except Exception as e:
            log.debug(f"Erro ao extrair parcelas pagas: {e}")

        if pagas is None:
            if self.config.pular_se_sem_emprestimo:
                log.info("     [Filtro Reprovado] Nenhum empréstimo ativo encontrado no benefício. Pulando.")
                return None
            pagas = 0

        if pagas > self.config.parcelas_pagas_limite:
            log.info(
                f"     [Filtro Reprovado] {pagas} parcelas pagas no último empréstimo "
                f"(limite é até {self.config.parcelas_pagas_limite} pagas). Pulando."
            )
            return None

        log.info(f"     [Filtro OK] Parcelas Pagas: {pagas} (<= {self.config.parcelas_pagas_limite})")

        # ── 3. Extração dos dois telefones ─────────────────────────
        self._desanonimizar_telefones()

        tel1_raw = "N/A"
        tel2_raw = "N/A"

        try:
            tels = page.evaluate("""() => {
                let t1 = '';
                let t2 = '';

                const divTel1 = document.querySelector('[data-telefonehot="1"]');
                const inpTel1 = document.querySelector('#telefonesHot-1') || document.querySelector('#telhot-1');
                if (divTel1 && divTel1.innerText.trim() && !divTel1.innerText.includes('*')) {
                    t1 = divTel1.innerText.trim();
                } else if (inpTel1 && inpTel1.value.trim()) {
                    t1 = inpTel1.value.trim();
                } else if (divTel1) {
                    t1 = divTel1.innerText.trim();
                }

                const divTel2 = document.querySelector('[data-telefonehot="2"]');
                const inpTel2 = document.querySelector('#telefonesHot-2');
                if (divTel2 && divTel2.innerText.trim() && !divTel2.innerText.includes('*')) {
                    t2 = divTel2.innerText.trim();
                } else if (inpTel2 && inpTel2.value.trim()) {
                    t2 = inpTel2.value.trim();
                } else if (divTel2) {
                    t2 = divTel2.innerText.trim();
                }

                return { t1, t2 };
            }""")
            if tels:
                tel1_raw = tels.get("t1", "N/A") or "N/A"
                tel2_raw = tels.get("t2", "N/A") or "N/A"
        except Exception as e:
            log.debug(f"Erro ao capturar telefones: {e}")

        tel1_fmt = formatar_telefone(tel1_raw)
        tel2_fmt = formatar_telefone(tel2_raw)

        log.info(f"     [Telefones] Tel 1: {tel1_fmt}  |  Tel 2: {tel2_fmt}")

        return ResultadoClienteFiltro(
            cpf=cpf_fmt,
            idade=idade,
            tel1=tel1_fmt,
            tel2=tel2_fmt,
            parcelas_pagas=pagas,
        )

    def buscar_e_filtrar_por_cpf(self, cpf_raw: str) -> List[ResultadoClienteFiltro]:
        """
        Consulta o CPF no Corban, percorre seus benefícios e aplica os filtros.
        Retorna lista de clientes aprovados.
        Lança LimiteConsultasExcedido se o portal acusar limite diário.
        """
        page = self.page
        cpf_limpo = limpar_cpf(cpf_raw)
        cpf_fmt = formatar_cpf(cpf_limpo)

        resultados: List[ResultadoClienteFiltro] = []

        try:
            self._buscar_cpf(cpf_limpo)

            alertas = page.evaluate("""() => {
                return Array.from(
                    document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body, .swal2-title')
                ).map(el => (el.innerText || '').trim());
            }""")
            if any("limite" in a.lower() for a in alertas):
                raise LimiteConsultasExcedido(
                    f"Limite diário atingido na conta '{self._credencial_ativa.usuario}'."
                )

            try:
                page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
            except PlaywrightTimeout:
                log.warning(f"  Nenhum benefício exibido para {cpf_fmt}")
                return resultados

            botoes_iniciais = page.query_selector_all(".selecionar--beneficio__btn")
            qtd = len(botoes_iniciais)
            if qtd == 0:
                log.warning(f"  Nenhum benefício encontrado para {cpf_fmt}")
                return resultados

            log.info(f"  → {qtd} benefício(s) encontrado(s)")

            for i in range(qtd):
                try:
                    page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
                    botoes = page.query_selector_all(".selecionar--beneficio__btn")

                    if i >= len(botoes):
                        log.warning(f"  Botão do benefício {i + 1} não encontrado; pulando.")
                        break

                    nome_beneficio = f"Benefício {i + 1}"
                    try:
                        rotulo = botoes[i].evaluate("""el => {
                            let cont = el.closest('[class*="beneficio"], [class*="card"], .panel, .box');
                            if (!cont) cont = el.parentElement;
                            return cont ? cont.innerText.split('\\n')[0].trim() : '';
                        }""")
                        if rotulo:
                            nome_beneficio = rotulo[:60]
                    except Exception:
                        pass

                    log.info(f"  Selecionando: {nome_beneficio}")
                    botoes[i].click()

                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    page.wait_for_timeout(self.config.tempo_espera * 1_000)

                    aprovado = self._extrair_e_filtrar_beneficio(cpf_fmt)
                    if aprovado:
                        resultados.append(aprovado)
                        log.info(f"  [★ APROVADO] CPF: {cpf_fmt} | Idade: {aprovado.idade} | Pagas: {aprovado.parcelas_pagas}")

                    page.go_back()
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    page.wait_for_timeout(1_500)

                    if i < qtd - 1:
                        self._buscar_cpf(cpf_limpo)

                except PlaywrightTimeout:
                    log.error(f"  Timeout no benefício {i + 1} do CPF {cpf_fmt}")
                except Exception as e:
                    log.error(f"  Erro no benefício {i + 1} do CPF {cpf_fmt}: {e}")
                    try:
                        page.go_back()
                        page.wait_for_timeout(2_000)
                        if i < qtd - 1:
                            self._buscar_cpf(cpf_limpo)
                    except Exception:
                        pass

            return resultados

        except LimiteConsultasExcedido:
            raise
        except Exception as e:
            msg_e = str(e)
            if "locator.click" in msg_e.lower() or isinstance(e, PlaywrightTimeout):
                raise ErroParadaImediata(f"Erro de Locator/Timeout no CPF {cpf_fmt}: {e}")
            log.error(f"  Erro ao consultar CPF {cpf_fmt}: {e}")
            raise ErroParadaImediata(f"Erro inesperado no CPF {cpf_fmt}: {e}")
