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
    ErroParadaImediata,
    formatar_cpf,
    limpar_cpf,
    limpar_telefone,
)

log = logging.getLogger(__name__)


class CorbanClient:
    """
    Adapter de automação do portal Sistema Corban.
    Encapsula o ciclo de vida do Playwright, autenticação e raspagem do modal de pesquisa.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def __enter__(self) -> "CorbanClient":
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.fechar()

    def iniciar(self) -> None:
        """Inicia o navegador, cria a página e realiza o login."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.config.modo_visivel,
            slow_mo=80,
        )
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = self._context.new_page()

        self._fazer_login()

    def fechar(self) -> None:
        """Encerra a sessão do navegador e limpa os recursos."""
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
        """Realiza autenticação no portal Corban."""
        page = self._page
        log.info("Acessando o portal CORBAN…")
        page.goto(self.config.site_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)

        log.info("Preenchendo credenciais…")
        page.fill("#exten", self.config.usuario)
        page.fill("#password", self.config.senha)
        page.click("#button-sigin")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(2_000)

        if page.query_selector("#button-sigin") or "login" in page.url.lower():
            raise RuntimeError("Falha no login! Verifique as credenciais USUARIO e SENHA.")

        log.info("✔ Login realizado com sucesso.")

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

            while time.time() - tempo_inicio < self.config.timeout_resposta:
                resultado = page.evaluate("""() => {
                    const linhas = Array.from(document.querySelectorAll('tr')).filter(tr => {
                        const links = tr.querySelectorAll('a[onclick*="levarcodigoRapido2"], a.btn-primary');
                        return links.length > 0;
                    });

                    if (linhas.length === 0) {
                        const alertas = Array.from(
                            document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body')
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

                    return { status: 'concluido', cpfs: dados };
                }""")

                if resultado.get("status") == "concluido" and resultado.get("cpfs"):
                    for raw_cpf in resultado["cpfs"]:
                        c_limpo = limpar_cpf(raw_cpf)
                        if len(c_limpo) == 11:
                            cpf_fmt = formatar_cpf(c_limpo)
                            if cpf_fmt not in cpfs_encontrados:
                                cpfs_encontrados.append(cpf_fmt)
                    break

                alertas = resultado.get("alertas", [])
                if any("nenhum" in a.lower() or "não encontrado" in a.lower() for a in alertas):
                    log.info("  Aviso: Nenhum cliente retornado para este telefone.")
                    break

                page.wait_for_timeout(600)

            return cpfs_encontrados

        except Exception as e:
            msg_e = str(e)
            if "locator.click" in msg_e.lower() or isinstance(e, PlaywrightTimeout):
                raise ErroParadaImediata(f"Erro de Locator/Timeout no telefone {telefone_raw}: {e}")
            log.error(f"  Erro ao consultar telefone {telefone_raw}: {e}")
            raise ErroParadaImediata(f"Erro inesperado no telefone {telefone_raw}: {e}")
