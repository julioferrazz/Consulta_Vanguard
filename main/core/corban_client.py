import logging
import time
from datetime import datetime
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
    ResultadoMargem,
    formatar_cpf,
    limpar_banco,
    limpar_cpf,
    limpar_margem,
)

log = logging.getLogger(__name__)


class CorbanClient:
    """
    Adapter de automação web para o portal Sistema Corban.
    Encapsula o ciclo de vida do Playwright, autenticação, rotação de contas,
    pesquisa de CPFs e extração de margens e bancos.
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
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(self.config.tempo_espera * 1_000)

    def _extrair_dados_beneficio(self) -> tuple[str, str]:
        """Extrai os campos de Banco e Margem Disponível da tela de benefício."""
        page = self.page
        banco_raw = "N/A"
        margem_raw = "N/A"

        try:
            res = page.evaluate(r"""() => {
                let banco = 'N/A';
                let margem = 'N/A';

                // 1. Extração do Banco pela linha cuja primeira célula contenha "Banco"
                const linhas = document.querySelectorAll('tr');
                for (const tr of linhas) {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length < 2) continue;
                    const rotulo = (tds[0].innerText || '').trim();
                    if (/^banco$/i.test(rotulo)) {
                        banco = (tds[1].innerText || '').trim();
                        break;
                    }
                }

                // 2. Extração da Margem Disponível (restrita ao bloco 'Margem livre')
                const reLivre = /margem\s+livre/i;
                const reLabel = /margem\s+dispon[íi]vel/i;
                const candidatos = document.querySelectorAll('body *');
                let melhorContainer = null;
                let melhorTamanho = Infinity;

                for (const el of candidatos) {
                    const txt = (el.innerText || '');
                    if (!txt || txt.length > 400) continue;
                    if (reLivre.test(txt) && reLabel.test(txt)) {
                        if (txt.length < melhorTamanho) {
                            melhorTamanho = txt.length;
                            melhorContainer = el;
                        }
                    }
                }

                if (melhorContainer) {
                    const texto = melhorContainer.innerText || '';
                    const m = /margem\s+dispon[íi]vel\s*:?\s*([-–—]?\s*(?:R\$)?\s*[-–—]?\s*\d+(?:[.,]\d+)*)/i.exec(texto);
                    if (m) margem = m[1].trim();
                }

                return { banco, margem };
            }""")

            if res:
                banco_raw = res.get("banco", "N/A") or "N/A"
                margem_raw = res.get("margem", "N/A") or "N/A"

        except Exception as e:
            log.error(f"Erro ao extrair dados do benefício via JS: {e}")

        banco = limpar_banco(banco_raw)
        margem = limpar_margem(margem_raw)

        if banco == "N/A" and margem == "N/A":
            ts = datetime.now().strftime("%H%M%S")
            try:
                page.screenshot(path=f"debug_{ts}.png")
                log.warning(f"⚠ Não foi possível extrair dados. Screenshot salvo: debug_{ts}.png")
            except Exception:
                pass

        return banco, margem

    def buscar_margens_por_cpf(self, cpf_raw: str) -> List[ResultadoMargem]:
        """
        Consulta o CPF no Corban e extrai todos os benefícios com seus respectivos Banco e Margem.
        Retorna lista de ResultadoMargem.
        Lança LimiteConsultasExcedido se o portal acusar limite diário.
        """
        page = self.page
        cpf_limpo = limpar_cpf(cpf_raw)
        cpf_fmt = formatar_cpf(cpf_limpo)

        resultados: List[ResultadoMargem] = []

        try:
            self._buscar_cpf(cpf_limpo)

            # Verifica alertas de limite diário ou mensagens modais
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

                    banco, margem = self._extrair_dados_beneficio()
                    resultados.append(ResultadoMargem(cpf=cpf_fmt, banco=banco, margem_disponivel=margem))
                    log.info(f"     Banco: {banco}  |  Margem: {margem}")

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
