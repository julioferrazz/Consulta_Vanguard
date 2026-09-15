#!/usr/bin/env python3
"""
============================================================
  CONSULTA MARGEM CORBAN - Automação de Consultas
============================================================
  Automatiza a consulta de margem disponível por CPF no
  sistema gestao.sistemacorban.com.br

  Requisitos: playwright, openpyxl
  Uso: python consulta_corban.py
============================================================
"""

import csv
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

# ============================================================
# ✏️  CONFIGURAÇÕES — Edite estas variáveis antes de executar
# ============================================================

USUARIO = "ti@37277"  # ← seu usuário de login
SENHA = "Safira@26"  # ← sua senha de login
ARQUIVO_ENTRADA = "cpfs.csv"  # ← arquivo com CPFs na 1ª coluna
ARQUIVO_SAIDA = "resultados.csv"  # ← arquivo de saída (gerado)
MODO_VISIVEL = True  # True = mostra o navegador
TEMPO_ESPERA = 2  # Segundos de pausa entre ações
LIMITE_CPFS = 500  # Máximo de CPFs pendentes a processar por execução
# (CPFs já presentes no arquivo de saída são pulados
#  e não contam nesse limite)

# ============================================================

SITE_URL = "https://gestao.sistemacorban.com.br"

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("consulta.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  Utilitários & Filtros de Limpeza
# ─────────────────────────────────────────────────────────────


def limpar_banco(texto: str) -> str:
    """Extrai apenas a string limpa do banco, formato NOME - 000."""
    if not texto or texto == "N/A":
        return "N/A"

    m = re.search(
        r"([A-ZÁÉÍÓÚÀÂÊÔÃÕÇÜa-záéíóúàâêôãõçü0-9\s\.\(\)\/,&\'_]+?)\s*[-–]\s*(\d{3})",
        texto,
        re.IGNORECASE,
    )
    if m:
        nome = m.group(1).strip()
        codigo = m.group(2).strip()
        return f"{nome} - {codigo}".upper()
    return texto.strip().upper()


def limpar_margem(texto: str) -> str:
    """Extrai estritamente o valor (ex: -74,14), convertendo pontos em vírgulas."""
    if not texto or texto == "N/A":
        return "N/A"

    # Procura opcionalmente o sinal (- ou –), símbolo de R$, outro sinal e o número
    m = re.search(
        r"([-–—]?)\s*(?:R\$\s*)?([-–—]?)\s*(\d+(?:[.,]\d+)*)", texto, re.IGNORECASE
    )
    if m:
        s1 = m.group(1).strip()
        s2 = m.group(2).strip()

        # Define se tem sinal negativo
        sinal = "-" if (s1 in ["-", "–", "—"] or s2 in ["-", "–", "—"]) else ""
        numero = m.group(3)

        # Limpa o formato PT-BR (remove ponto de milhar, mantem vírgula para decimal)
        if "." in numero and "," in numero:
            numero = numero.replace(".", "")
        elif "." in numero and "," not in numero:
            numero = numero.replace(".", ",")

        return f"{sinal}{numero}"

    return "N/A"


def ler_cpfs(arquivo: str) -> list[str]:
    """Lê CPFs da primeira coluna de um arquivo CSV ou Excel (.xlsx)."""
    cpfs = []
    ext = Path(arquivo).suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(min_row=1, values_only=True):
                if row[0] is not None:
                    cpfs.append(str(row[0]).strip())
            wb.close()
        else:  # CSV / TXT
            with open(arquivo, "r", encoding="utf-8-sig") as f:
                for linha in csv.reader(f):
                    if linha and linha[0].strip():
                        cpfs.append(linha[0].strip())
    except Exception as e:
        log.error(f"Erro ao ler o arquivo de entrada: {e}")
        sys.exit(1)

    # Remove duplicatas mantendo a ordem (comparando versão limpa e preenchida)
    vistos = set()
    unicos = []
    for c in cpfs:
        cpf_limpo = limpar_cpf(c)
        if cpf_limpo not in vistos:
            vistos.add(cpf_limpo)
            unicos.append(c)

    log.info(f"{len(unicos)} CPF(s) carregado(s) de '{arquivo}'")
    return unicos


def limpar_cpf(cpf: str) -> str:
    """Remove tudo que não for dígito e preenche com zeros à esquerda."""
    digitos = "".join(filter(str.isdigit, str(cpf)))
    if digitos and len(digitos) <= 11:
        return digitos.zfill(11)
    return digitos


def formatar_cpf(cpf: str) -> str:
    """Retorna o CPF formatado: 000.000.000-00"""
    c = limpar_cpf(cpf)
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return cpf


def ler_cpfs_processados(arquivo: str) -> set[str]:
    """Retorna conjunto de CPFs já processados lendo o arquivo de saída."""
    processados: set[str] = set()
    if not os.path.exists(arquivo):
        return processados
    try:
        with open(arquivo, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cpf = limpar_cpf(row.get("CPF", ""))
                if cpf:
                    processados.add(cpf)
        log.info(
            f"{len(processados)} CPF(s) já encontrados em '{arquivo}' — serão pulados"
        )
    except Exception as e:
        log.warning(f"Não foi possível ler o arquivo de saída existente: {e}")
    return processados


def salvar_novos_resultados(novos: list[dict], arquivo: str) -> None:
    """Adiciona novos registros ao arquivo CSV. Cria o arquivo se não existir."""
    if not novos:
        return

    campos = ["CPF", "Banco", "Margem_Disponivel"]
    arquivo_existe = os.path.exists(arquivo)

    with open(arquivo, "a", newline="", encoding="utf-8-sig") as f:
        # Usa separador padrão (vírgula) para ser estritamente compatível com o formato CSV esperado
        writer = csv.DictWriter(f, fieldnames=campos, quoting=csv.QUOTE_MINIMAL)
        if not arquivo_existe:
            writer.writeheader()
        writer.writerows(novos)

    log.info(f"{len(novos)} novo(s) registro(s) adicionado(s) em '{arquivo}'")


# ─────────────────────────────────────────────────────────────
#  Ações de navegação
# ─────────────────────────────────────────────────────────────


def fazer_login(page) -> None:
    """Acessa o site e realiza o login."""
    log.info("Acessando o site CORBAN…")
    page.goto(SITE_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=30_000)

    log.info("Preenchendo credenciais…")
    page.fill("#exten", USUARIO)
    page.fill("#password", SENHA)
    page.click("#button-sigin")

    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(2_000)

    if page.query_selector("#button-sigin") or "login" in page.url.lower():
        raise RuntimeError("Login falhou! Verifique as variáveis USUARIO e SENHA.")

    log.info("✔ Login realizado com sucesso.")


def buscar_cpf(page, cpf_limpo: str) -> None:
    """Garante que o filtro esteja em CPF e executa a busca."""
    try:
        page.select_option("#typeOfDataSent", value="cpf")
        page.wait_for_timeout(400)
    except Exception:
        pass

    campo = page.locator("#dataToReceptivo")
    campo.click()
    campo.fill("")
    campo.type(cpf_limpo, delay=40)
    page.wait_for_timeout(400)

    page.click("#btnConsultaClienteRapido")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(TEMPO_ESPERA * 1_000)


def extrair_dados_beneficio(page) -> tuple[str, str]:
    """Extrai dados brutos via JS e refina no Python para o padrão final.

    Agora os dados são lidos exatamente dos elementos corretos da página:

    - Banco: da linha da tabela cujo rótulo é "Banco", ex.:
      <tr><td><b>Banco</b></td><td class="text-right text-secundary__system">104</td></tr>

    - Margem disponível: da linha específica "Margem livre", que contém o
      rótulo "$ Margem Disponível:" seguido do valor, ex.:
      Margem livre ⭐  $ Margem Disponível: - 7,80
      (existem outras linhas com o mesmo rótulo "Margem Disponível" para
      outros produtos, como Cartão RMC — por isso a busca é restrita ao
      menor elemento que contenha tanto "Margem livre" quanto "Margem
      Disponível" ao mesmo tempo, evitando pegar o valor errado)
    """
    banco_raw = "N/A"
    margem_raw = "N/A"

    try:
        res = page.evaluate(r"""() => {
            let banco  = 'N/A';
            let margem = 'N/A';

            // ── Banco: linha <tr> cujo primeiro <td> tem o rótulo "Banco" ──
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

            // ── Margem disponível: menor elemento que contenha tanto
            //    "Margem livre" quanto "Margem Disponível" — isso garante
            //    que pegamos a linha certa e não a de outro produto
            //    (ex: Cartão RMC) que usa o mesmo rótulo.
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

    # Passa o dado cru pelos filtros em Python para garantir precisão e formato!
    banco = limpar_banco(banco_raw)
    margem = limpar_margem(margem_raw)

    if banco == "N/A" and margem == "N/A":
        ts = datetime.now().strftime("%H%M%S")
        page.screenshot(path=f"debug_{ts}.png")
        log.warning(
            f"⚠ Não foi possível extrair dados. Screenshot salvo: debug_{ts}.png"
        )

    return banco, margem


# ─────────────────────────────────────────────────────────────
#  Processamento por CPF
# ─────────────────────────────────────────────────────────────


def processar_cpf(page, cpf_raw: str, resultados: list[dict]) -> None:
    cpf_limpo = limpar_cpf(cpf_raw)
    cpf_fmt = formatar_cpf(cpf_limpo)

    if len(cpf_limpo) != 11:
        log.warning(f"  CPF ignorado (formato inválido): {cpf_raw!r}")
        return

    try:
        buscar_cpf(page, cpf_limpo)

        try:
            page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
        except PlaywrightTimeout:
            log.warning(f"  Nenhum benefício exibido para {cpf_fmt}")
            return

        qtd = len(page.query_selector_all(".selecionar--beneficio__btn"))
        if qtd == 0:
            log.warning(f"  Nenhum benefício encontrado para {cpf_fmt}")
            return

        log.info(f"  → {qtd} benefício(s) encontrado(s)")

        for i in range(qtd):
            try:
                page.wait_for_selector(".selecionar--beneficio__btn", timeout=10_000)
                botoes = page.query_selector_all(".selecionar--beneficio__btn")

                if i >= len(botoes):
                    log.warning(f"  Botão {i+1} não encontrado; pulando.")
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
                page.wait_for_timeout(TEMPO_ESPERA * 1_000)

                banco, margem = extrair_dados_beneficio(page)

                resultados.append(
                    {
                        "CPF": cpf_fmt,
                        "Banco": banco,
                        "Margem_Disponivel": margem,
                    }
                )
                log.info(f"     Banco: {banco}  |  Margem: {margem}")

                page.go_back()
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                page.wait_for_timeout(1_500)

                if i < qtd - 1:
                    buscar_cpf(page, cpf_limpo)

            except PlaywrightTimeout:
                log.error(f"  Timeout no benefício {i+1} do CPF {cpf_fmt}")
            except Exception as e:
                log.error(f"  Erro no benefício {i+1} do CPF {cpf_fmt}: {e}")
                try:
                    page.go_back()
                    page.wait_for_timeout(2_000)
                    if i < qtd - 1:
                        buscar_cpf(page, cpf_limpo)
                except Exception:
                    pass

    except Exception as e:
        log.error(f"  Erro ao processar CPF {cpf_fmt}: {e}")


# ─────────────────────────────────────────────────────────────
#  Ponto de entrada
# ─────────────────────────────────────────────────────────────


def main() -> None:
    log.info("=" * 60)
    log.info("  CONSULTA MARGEM CORBAN")
    log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info("=" * 60)

    if USUARIO in ("SEU_USUARIO", "") or SENHA in ("SUA_SENHA", ""):
        log.error("Configure USUARIO e SENHA no topo do arquivo consulta_corban.py!")
        sys.exit(1)

    if not os.path.exists(ARQUIVO_ENTRADA):
        log.error(f"Arquivo de entrada não encontrado: '{ARQUIVO_ENTRADA}'")
        sys.exit(1)

    todos_cpfs = ler_cpfs(ARQUIVO_ENTRADA)
    if not todos_cpfs:
        log.error("Nenhum CPF encontrado no arquivo de entrada!")
        sys.exit(1)

    ja_processados = ler_cpfs_processados(ARQUIVO_SAIDA)
    cpfs_pendentes = [c for c in todos_cpfs if limpar_cpf(c) not in ja_processados]

    if not cpfs_pendentes:
        log.info(
            "Todos os CPFs do arquivo de entrada já foram processados. Nada a fazer."
        )
        sys.exit(0)

    if len(cpfs_pendentes) > LIMITE_CPFS:
        log.info(
            f"{len(cpfs_pendentes)} CPF(s) pendentes — limitando a {LIMITE_CPFS} nesta execução."
        )
        cpfs_pendentes = cpfs_pendentes[:LIMITE_CPFS]
    else:
        log.info(f"{len(cpfs_pendentes)} CPF(s) pendentes nesta execução")

    total = len(cpfs_pendentes)
    novos_resultados: list[dict] = []
    ultimo_salvo = 0

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(
            headless=not MODO_VISIVEL,
            slow_mo=80,
        )
        contexto = navegador.new_context(viewport={"width": 1280, "height": 800})
        pagina = contexto.new_page()

        try:
            fazer_login(pagina)

            for idx, cpf in enumerate(cpfs_pendentes, start=1):
                log.info(f"\n[{idx}/{total}] CPF: {formatar_cpf(limpar_cpf(cpf))}")
                processar_cpf(pagina, cpf, novos_resultados)

                if idx % 10 == 0:
                    novos_trecho = novos_resultados[ultimo_salvo:]
                    salvar_novos_resultados(novos_trecho, ARQUIVO_SAIDA)
                    ultimo_salvo = len(novos_resultados)
                    log.info(
                        f"  ✔ Checkpoint: {ultimo_salvo} novo(s) registro(s) no arquivo"
                    )

        except KeyboardInterrupt:
            log.info("\nInterrompido pelo usuário (Ctrl+C).")
        except RuntimeError as e:
            log.error(str(e))
        except Exception as e:
            log.error(f"Erro inesperado: {e}")
        finally:
            restantes = novos_resultados[ultimo_salvo:]
            salvar_novos_resultados(restantes, ARQUIVO_SAIDA)
            contexto.close()
            navegador.close()

    log.info("\n" + "=" * 60)
    log.info(f"  CONCLUÍDO — {len(novos_resultados)} novo(s) registro(s) adicionado(s)")
    log.info(f"  Arquivo de saída: {ARQUIVO_SAIDA}")
    log.info(f"  Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
