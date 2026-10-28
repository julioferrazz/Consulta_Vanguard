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
    """Adapter de automação web Playwright para o portal Sistema Corban."""

    def __init__(self, config: AppConfig, credencial_inicial: Optional[Credencial] = None):
        self.config = config
        self._credencial_ativa = credencial_inicial or (config.contas[0] if config.contas else Credencial("", ""))
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def credencial_ativa(self) -> Credencial:
        return self._credencial_ativa

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Página não inicializada. Chame iniciar() antes.")
        return self._page

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RuntimeError("Navegador não inicializado. Chame iniciar() antes.")
        return self._browser

    def __enter__(self) -> "CorbanClient":
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.fechar()

    def iniciar(self) -> None:
        """Inicia o Chromium e realiza autenticação inicial."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self.config.modo_visivel, slow_mo=80)
        self._criar_contexto_e_logar()

    def _criar_contexto_e_logar(self) -> None:
        """Cria contexto isolado e efetua login aceitando alertas automáticos."""
        self._context = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = self._context.new_page()
        self._page.on("dialog", lambda dialog: dialog.accept())
        self._fazer_login()

    def alternar_conta(self, nova_credencial: Credencial) -> None:
        """Alterna a conta ativa fechando o contexto anterior e logando com nova credencial."""
        log.info(f"🔄 Alternando conta de '{self._credencial_ativa.usuario}' para '{nova_credencial.usuario}'...")
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        self._credencial_ativa = nova_credencial
        self._criar_contexto_e_logar()
        log.info(f"✔ Sessão iniciada com sucesso na conta '{nova_credencial.usuario}'.")

    def fechar(self) -> None:
        """Encerra a sessão e libera recursos do navegador."""
        for recurso in (self._context, self._browser, self._pw):
            if recurso:
                try:
                    recurso.close() if hasattr(recurso, "close") else recurso.stop()
                except Exception:
                    pass

    def _fazer_login(self) -> None:
        """Realiza autenticação no portal Corban."""
        page = self.page
        log.info(f"Acessando o portal CORBAN como '{self._credencial_ativa.usuario}'…")
        page.goto(self.config.site_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)

        page.fill("#exten", self._credencial_ativa.usuario)
        page.fill("#password", self._credencial_ativa.senha)
        page.click("#button-sigin")

        try:
            page.wait_for_url("**/index.php/**", timeout=20_000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2_000)

        if page.query_selector("#button-sigin") or "login" in page.url.lower():
            raise RuntimeError(f"Falha no login com o usuário '{self._credencial_ativa.usuario}'!")
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
        """Dispara a liberação dos números ocultos de telefone."""
        try:
            if (btn := self.page.query_selector("#btnHideOrShowTelefone")) and btn.is_visible():
                btn.click()
                self.page.wait_for_timeout(800)
            else:
                self.page.evaluate("() => typeof abrirCamposTelefones === 'function' && abrirCamposTelefones()")
                self.page.wait_for_timeout(500)
        except Exception as e:
            log.debug(f"Aviso ao liberar telefones: {e}")

    def _extrair_e_filtrar_beneficio(self, cpf_fmt: str) -> Optional[ResultadoClienteFiltro]:
        """Extrai dados do benefício e valida filtros de idade e parcelas pagas."""
        page = self.page

        # 1. Filtro de Idade
        nasc_el = page.query_selector("#cliNascimento")
        nasc_raw = nasc_el.inner_text().strip() if nasc_el else ""
        idade = calcular_idade(nasc_raw)

        if idade is None:
            log.warning(f"     ⚠ Idade não identificada ('{nasc_raw}'). Pulando benefício.")
            return None
        if idade >= self.config.idade_limite:
            log.info(f"     [Filtro Reprovado] Idade {idade} anos >= {self.config.idade_limite}. Pulando.")
            return None
        log.info(f"     [Filtro OK] Idade: {idade} anos (< {self.config.idade_limite})")

        # 2. Filtro de Parcelas Pagas no Último Empréstimo
        pagas = page.evaluate("""() => {
            const inp = document.querySelector('#linhaBanco--pagas_0');
            if (inp && inp.value !== '') {
                const num = parseInt(inp.value.trim(), 10);
                if (!isNaN(num)) return num;
            }
            const linha0 = document.querySelector('#titlelinha_0') || document.querySelector('.portlet-banco');
            if (linha0) {
                const m = /(\\d+)\\s*pagas/i.exec(linha0.innerText || '');
                if (m) return parseInt(m[1], 10);
            }
            return null;
        }""")

        if pagas is None:
            if self.config.pular_se_sem_emprestimo:
                log.info("     [Filtro Reprovado] Nenhum empréstimo ativo encontrado. Pulando.")
                return None
            pagas = 0

        if pagas > self.config.parcelas_pagas_limite:
            log.info(f"     [Filtro Reprovado] {pagas} parcelas pagas > limite {self.config.parcelas_pagas_limite}. Pulando.")
            return None
        log.info(f"     [Filtro OK] Parcelas Pagas: {pagas} (<= {self.config.parcelas_pagas_limite})")

        # 3. Extração e Formatação dos Telefones
        self._desanonimizar_telefones()
        tels = page.evaluate("""() => {
            const getVal = (hotNum, selInp) => {
                const div = document.querySelector(`[data-telefonehot="${hotNum}"]`);
                const inp = document.querySelector(selInp);
                if (div && div.innerText.trim() && !div.innerText.includes('*')) return div.innerText.trim();
                if (inp && inp.value.trim()) return inp.value.trim();
                return div ? div.innerText.trim() : '';
            };
            return {
                t1: getVal(1, '#telefonesHot-1, #telhot-1'),
                t2: getVal(2, '#telefonesHot-2')
            };
        }""") or {}

        tel1_fmt = formatar_telefone(tels.get("t1", "N/A"))
        tel2_fmt = formatar_telefone(tels.get("t2", "N/A"))
        log.info(f"     [Telefones] Tel 1: {tel1_fmt}  |  Tel 2: {tel2_fmt}")

        return ResultadoClienteFiltro(cpf=cpf_fmt, idade=idade, tel1=tel1_fmt, tel2=tel2_fmt, parcelas_pagas=pagas)

    def _recuperar_posicao(self, cpf_limpo: str, mais_itens: bool) -> None:
        """Retorna para a tela de listagem de benefícios com segurança."""
        try:
            self.page.go_back()
            try:
                self.page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            self.page.wait_for_timeout(1_500)
            if mais_itens:
                self._buscar_cpf(cpf_limpo)
        except Exception:
            pass

    def buscar_e_filtrar_por_cpf(self, cpf_raw: str) -> List[ResultadoClienteFiltro]:
        """Consulta o CPF no Corban, percorre benefícios e retorna clientes aprovados."""
        page = self.page
        cpf_limpo = limpar_cpf(cpf_raw)
        cpf_fmt = formatar_cpf(cpf_limpo)
        resultados: List[ResultadoClienteFiltro] = []

        try:
            self._buscar_cpf(cpf_limpo)

            alertas = page.evaluate("""() => Array.from(
                document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body, .swal2-title')
            ).map(el => (el.innerText || '').trim())""")
            if any("limite" in a.lower() for a in alertas):
                raise LimiteConsultasExcedido(f"Limite diário atingido na conta '{self._credencial_ativa.usuario}'.")

            try:
                page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
            except PlaywrightTimeout:
                log.warning(f"  Nenhum benefício exibido para {cpf_fmt}")
                return resultados

            qtd = len(page.query_selector_all(".selecionar--beneficio__btn"))
            if qtd == 0:
                log.warning(f"  Nenhum benefício encontrado para {cpf_fmt}")
                return resultados

            log.info(f"  → {qtd} benefício(s) encontrado(s)")

            for i in range(qtd):
                try:
                    page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
                    botoes = page.query_selector_all(".selecionar--beneficio__btn")
                    if i >= len(botoes):
                        break

                    nome_beneficio = f"Benefício {i + 1}"
                    try:
                        rotulo = botoes[i].evaluate("""el => {
                            let c = el.closest('[class*="beneficio"], [class*="card"], .panel, .box') || el.parentElement;
                            return c ? c.innerText.split('\\n')[0].trim() : '';
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

                    if aprovado := self._extrair_e_filtrar_beneficio(cpf_fmt):
                        resultados.append(aprovado)
                        log.info(f"  [★ APROVADO] CPF: {cpf_fmt} | Idade: {aprovado.idade} | Pagas: {aprovado.parcelas_pagas}")

                    self._recuperar_posicao(cpf_limpo, i < qtd - 1)

                except PlaywrightTimeout:
                    log.error(f"  Timeout no benefício {i + 1} do CPF {cpf_fmt}")
                    self._recuperar_posicao(cpf_limpo, i < qtd - 1)
                except Exception as e:
                    log.error(f"  Erro no benefício {i + 1} do CPF {cpf_fmt}: {e}")
                    self._recuperar_posicao(cpf_limpo, i < qtd - 1)

            return resultados

        except LimiteConsultasExcedido:
            raise
        except Exception as e:
            if "locator.click" in str(e).lower() or isinstance(e, PlaywrightTimeout):
                raise ErroParadaImediata(f"Erro de Locator/Timeout no CPF {cpf_fmt}: {e}")
            raise ErroParadaImediata(f"Erro inesperado no CPF {cpf_fmt}: {e}")
