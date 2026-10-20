#!/usr/bin/env python3
"""
============================================================
  CONSULTA CORBAN - Busca por TELEFONE e Extração de CPF
============================================================
  Automatiza a consulta de clientes por Telefone no
  sistema gestao.sistemacorban.com.br e extrai o CPF correspondente.

  Requisitos: playwright, openpyxl
  Uso: python consulta_corban.py
============================================================
"""

import csv
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

# ============================================================
# ✏️  CONFIGURAÇÕES — Edite estas variáveis se necessário
# ============================================================

USUARIO = "ti@37277"  # ← seu usuário de login
SENHA = "Safira@26"  # ← sua senha de login

# Nome padrão do arquivo de entrada (telefones na 1ª coluna)
ARQUIVO_ENTRADA = "telefones-base safira-menos juros.csv"
ARQUIVO_SAIDA = "resultados_telefones.csv"  # ← arquivo de saída gerado (Telefone, CPF)
ARQUIVO_HISTORICO = "telefones_consultados.txt"  # ← histórico de telefones já consultados

MODO_VISIVEL = True  # True = mostra o navegador
TEMPO_ENTRE_BUSCAS = 10  # Segundos obrigatórios de espera entre pesquisas
TIMEOUT_RESPOSTA = 20  # Segundos limite aguardando resultado da busca
LIMITE_REGISTROS = 5000  # Máximo de telefones pendentes a processar por execução

# ── Colunas do Arquivo de Saída ───────────────────────────────
CAMPOS_SAIDA = ["Telefone", "CPF"]

SITE_URL = "https://gestao.sistemacorban.com.br"

# ============================================================
#  Logging & Encodings
# ============================================================
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("consulta.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ============================================================
#  Exceções de Controle de Fluxo
# ============================================================
class LimiteConsultasExcedido(Exception):
    """Lançada quando a busca excede o timeout de resposta do sistema."""
    pass


class ErroParadaImediata(Exception):
    """Lançada quando ocorre erro crítico exigindo parada imediata sem marcar o telefone."""
    pass


# ============================================================
#  Utilitários de Limpeza e Formatação
# ============================================================
def limpar_telefone(tel: str) -> str:
    """Extrai apenas os dígitos numéricos e remove código 55 do Brasil se presente."""
    if not tel:
        return ""
    digitos = "".join(filter(str.isdigit, str(tel)))
    if (len(digitos) == 12 or len(digitos) == 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def formatar_telefone(tel: str) -> str:
    """Formata telefone em (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    digitos = limpar_telefone(tel)
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    elif len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return tel.strip() if tel else ""


def limpar_cpf(cpf: str) -> str:
    """Remove tudo que não for dígito e preenche com zeros à esquerda até 11 dígitos."""
    digitos = "".join(filter(str.isdigit, str(cpf)))
    if digitos and len(digitos) <= 11:
        return digitos.zfill(11)
    return digitos


def formatar_cpf(cpf: str) -> str:
    """Retorna CPF formatado no padrão 000.000.000-00."""
    c = limpar_cpf(cpf)
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return cpf


# ============================================================
#  Manipulação de Arquivos
# ============================================================
def obter_arquivo_entrada() -> str:
    """Retorna o caminho do arquivo de entrada existente, com fallbacks."""
    if os.path.exists(ARQUIVO_ENTRADA):
        return ARQUIVO_ENTRADA

    candidatos = [
        "telefones-base safira-menos juros.csv",
        "telefones.csv",
        "telefones.xlsx",
        "telefones.txt",
    ]
    for c in candidatos:
        if os.path.exists(c):
            return c

    # Procura qualquer arquivo csv que contenha 'telefone' no nome
    for arq in os.listdir("."):
        if arq.lower().endswith((".csv", ".xlsx")) and "telefone" in arq.lower():
            return arq

    return ARQUIVO_ENTRADA


def ler_telefones(arquivo: str) -> list[str]:
    """Lê telefones da primeira coluna de arquivo CSV ou Excel, pulando cabeçalho."""
    tels = []
    ext = Path(arquivo).suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and row[0] is not None:
                    val = str(row[0]).strip()
                    if val:
                        tels.append(val)
            wb.close()
        else:
            with open(arquivo, "r", encoding="utf-8-sig", errors="replace") as f:
                # Detecta delimitador (vírgula, ponto e vírgula ou tab)
                primeira_linha = f.readline()
                delimitador = ";" if ";" in primeira_linha else ","
                f.seek(0)

                leitor = csv.reader(f, delimiter=delimitador)
                next(leitor, None)  # Pula o cabeçalho
                for linha in leitor:
                    if linha and linha[0].strip():
                        tels.append(linha[0].strip())
    except Exception as e:
        log.error(f"Erro ao ler o arquivo de entrada '{arquivo}': {e}")
        sys.exit(1)

    vistos = set()
    unicos = []
    for t in tels:
        t_limpo = limpar_telefone(t)
        if len(t_limpo) in (10, 11) and t_limpo not in vistos:
            vistos.add(t_limpo)
            unicos.append(t)

    log.info(f"{len(unicos)} telefone(s) válido(s) carregado(s) de '{arquivo}'")
    return unicos


def registrar_telefone_consultado(tel: str, arquivo: str = ARQUIVO_HISTORICO) -> None:
    """Registra o telefone no histórico para evitar reconsultas futuras."""
    t_limpo = limpar_telefone(tel)
    if not t_limpo:
        return
    try:
        with open(arquivo, "a", encoding="utf-8") as f:
            f.write(f"{t_limpo}\n")
    except Exception as e:
        log.debug(f"Aviso ao registrar histórico: {e}")


def ler_telefones_processados(arquivo_saida: str = ARQUIVO_SAIDA, arquivo_historico: str = ARQUIVO_HISTORICO) -> set[str]:
    """Retorna conjunto de telefones já processados (tanto do CSV de saída quanto do histórico)."""
    processados: set[str] = set()

    if os.path.exists(arquivo_saida):
        try:
            with open(arquivo_saida, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    t = limpar_telefone(row.get("Telefone", ""))
                    if t:
                        processados.add(t)
        except Exception as e:
            log.warning(f"Aviso ao ler arquivo de saída: {e}")

    if os.path.exists(arquivo_historico):
        try:
            with open(arquivo_historico, "r", encoding="utf-8") as f:
                for linha in f:
                    t = limpar_telefone(linha.strip())
                    if t:
                        processados.add(t)
        except Exception as e:
            log.warning(f"Aviso ao ler arquivo de histórico: {e}")

    if processados:
        log.info(f"{len(processados)} telefone(s) já consultados anteriormente — serão pulados")
    return processados


def inicializar_arquivo_saida(arquivo: str) -> None:
    """Garante a existência do arquivo CSV com cabeçalho correto em 2 colunas separadas."""
    if not os.path.exists(arquivo):
        with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
        log.info(f"Arquivo de saída '{arquivo}' criado com cabeçalho: {CAMPOS_SAIDA}")


def salvar_novos_resultados(novos: list[dict], arquivo: str) -> None:
    """Adiciona novos registros ao arquivo CSV de saída."""
    inicializar_arquivo_saida(arquivo)
    if not novos:
        return

    with open(arquivo, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
        writer.writerows(novos)
    log.info(f"{len(novos)} novo(s) registro(s) adicionado(s) em '{arquivo}'")


# ============================================================
#  Navegação no Portal Corban
# ============================================================
def fazer_login(page) -> None:
    """Acessa o site e realiza o login com as credenciais configuradas."""
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


def abrir_modal_busca_telefone(page) -> None:
    """Clica no botão de busca por telefone para abrir a caixa de pesquisa."""
    seletor_input = "#telefoneBuscaTelefoneConsultaRapida"
    try:
        inp = page.locator(seletor_input)
        if inp.is_visible():
            return
    except Exception:
        pass

    # Tenta clicar no botão de busca por telefone
    seletor_botao = "button[onclick*='buscarPorIdRapida'], button[data-original-title*='telefone'], button:has(i.fa-phone)"
    try:
        btn = page.locator(seletor_botao).first
        if btn.is_visible():
            btn.click()
        else:
            page.evaluate("if (typeof buscarPorIdRapida === 'function') buscarPorIdRapida();")
    except Exception:
        page.evaluate("if (typeof buscarPorIdRapida === 'function') buscarPorIdRapida();")

    page.wait_for_selector(seletor_input, state="visible", timeout=8_000)


def consultar_telefone(page, tel_limpo: str) -> list[str]:
    """
    Realiza a pesquisa pelo telefone informado e extrai o(s) CPF(s) retornado(s).
    Retorna uma lista de CPFs encontrados (formatados).
    """
    abrir_modal_busca_telefone(page)

    campo_tel = page.locator("#telefoneBuscaTelefoneConsultaRapida")
    campo_tel.click()
    campo_tel.fill("")
    campo_tel.type(tel_limpo, delay=40)
    page.wait_for_timeout(300)

    btn_buscar = page.locator("#btnBuscarcpfPorCliente")
    btn_buscar.click()

    # Aguarda a resposta (tabela com resultados ou aviso de não encontrado)
    tempo_inicio = time.time()
    cpfs_encontrados: list[str] = []

    while time.time() - tempo_inicio < TIMEOUT_RESPOSTA:
        # Avalia se há linhas com resultados na tabela do modal
        resultado = page.evaluate("""() => {
            // Procura linhas que contenham o link de selecionar benefício ou estrutura da tabela rápida
            const linhas = Array.from(document.querySelectorAll('tr')).filter(tr => {
                const links = tr.querySelectorAll('a[onclick*="levarcodigoRapido2"], a.btn-primary');
                return links.length > 0;
            });

            if (linhas.length === 0) {
                // Verifica se há alguma mensagem de alerta/nenhum resultado
                const alertas = Array.from(document.querySelectorAll('.swal2-content, .toast-message, .alert, .bootbox-body')).map(el => el.innerText.trim());
                return { status: 'aguardando', linhas: [], alertas: alertas };
            }

            // Descobre o índice da coluna de CPF a partir dos cabeçalhos, se existirem
            let colCpfIndex = 0; // Padrão baseado no HTML fornecido
            const ths = Array.from(document.querySelectorAll('th')).map(th => th.innerText.toLowerCase().trim());
            const idxTh = ths.findIndex(t => t.includes('cpf'));
            if (idxTh !== -1) {
                colCpfIndex = idxTh;
            }

            const dados = [];
            for (const tr of linhas) {
                const tds = tr.querySelectorAll('td');
                if (tds.length >= 2) {
                    const txtCpf = tds[colCpfIndex] ? tds[colCpfIndex].innerText.trim() : '';
                    if (txtCpf) {
                        dados.push(txtCpf);
                    }
                }
            }

            return { status: 'concluido', cpfs: dados };
        }""")

        if resultado.get("status") == "concluido" and resultado.get("cpfs"):
            for raw_cpf in resultado["cpfs"]:
                cpf_limpo = limpar_cpf(raw_cpf)
                if len(cpf_limpo) == 11:
                    cpf_fmt = formatar_cpf(cpf_limpo)
                    if cpf_fmt not in cpfs_encontrados:
                        cpfs_encontrados.append(cpf_fmt)
            break

        # Verifica se apareceu alerta de "nenhum registro encontrado"
        alertas = resultado.get("alertas", [])
        if any("nenhum" in a.lower() or "não encontrado" in a.lower() for a in alertas):
            log.info("  Aviso: Nenhum cliente encontrado para este telefone.")
            break

        page.wait_for_timeout(600)

    return cpfs_encontrados


def processar_telefone(page, tel_raw: str, resultados: list[dict]) -> None:
    """Executa a busca do telefone, extrai CPFs e adiciona à lista de resultados."""
    tel_limpo = limpar_telefone(tel_raw)
    tel_fmt = formatar_telefone(tel_raw)

    if len(tel_limpo) not in (10, 11):
        log.warning(f"  Telefone ignorado (formato inválido): {tel_raw!r}")
        return

    try:
        cpfs = consultar_telefone(page, tel_limpo)

        if not cpfs:
            log.info(f"  [-] Nenhum CPF retornado para o telefone {tel_fmt}")
        else:
            for cpf in cpfs:
                registro = {
                    "Telefone": tel_fmt,
                    "CPF": cpf,
                }
                resultados.append(registro)
                log.info(f"  [★ ENCONTRADO] Telefone: {tel_fmt} | CPF: {cpf}")

    except Exception as e:
        msg_e = str(e)
        if "locator.click" in msg_e.lower() or isinstance(e, PlaywrightTimeout):
            raise ErroParadaImediata(f"Erro de Locator/Timeout no telefone {tel_fmt}: {e}")
        log.error(f"  Erro ao processar telefone {tel_fmt}: {e}")
        raise ErroParadaImediata(f"Erro inesperado no telefone {tel_fmt}: {e}")


# ============================================================
#  Função Principal
# ============================================================
def main() -> None:
    log.info("=" * 60)
    log.info("  CONSULTA CORBAN - BUSCA POR TELEFONE -> CPF")
    log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info("=" * 60)

    if USUARIO in ("SEU_USUARIO", "") or SENHA in ("SUA_SENHA", ""):
        log.error("Configure USUARIO e SENHA no topo do script!")
        sys.exit(1)

    arquivo_in = obter_arquivo_entrada()
    if not os.path.exists(arquivo_in):
        log.error(
            f"Arquivo de entrada não encontrado: '{ARQUIVO_ENTRADA}'\n"
            f"Coloque o arquivo '{ARQUIVO_ENTRADA}' com telefones na 1ª coluna na pasta 'main'."
        )
        sys.exit(1)

    todos_telefones = ler_telefones(arquivo_in)
    if not todos_telefones:
        log.error("Nenhum telefone válido encontrado no arquivo de entrada!")
        sys.exit(1)

    inicializar_arquivo_saida(ARQUIVO_SAIDA)
    ja_processados = ler_telefones_processados(ARQUIVO_SAIDA, ARQUIVO_HISTORICO)
    pendentes = [t for t in todos_telefones if limpar_telefone(t) not in ja_processados]

    if not pendentes:
        log.info("Todos os telefones do arquivo de entrada já foram consultados. Nada a fazer.")
        sys.exit(0)

    if len(pendentes) > LIMITE_REGISTROS:
        log.info(f"{len(pendentes)} telefone(s) pendentes — limitando a {LIMITE_REGISTROS} nesta execução.")
        pendentes = pendentes[:LIMITE_REGISTROS]
    else:
        log.info(f"{len(pendentes)} telefone(s) pendentes nesta execução")

    total = len(pendentes)
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

            for idx, tel in enumerate(pendentes, start=1):
                tel_fmt = formatar_telefone(tel)
                log.info(f"\n[{idx}/{total}] Pesquisando Telefone: {tel_fmt}")

                try:
                    processar_telefone(pagina, tel, novos_resultados)
                    # Registra no histórico somente se a busca concluiu sem erro crítico
                    registrar_telefone_consultado(tel)
                except ErroParadaImediata as e:
                    log.error(f"\n[ERRO DE EXECUÇÃO DETECTADO] {e}")
                    log.warning(f"O telefone {tel_fmt} NÃO foi marcado como consultado para tentar novamente depois.")
                    log.warning("Encerrando o programa para segurança.")
                    break
                except Exception as e:
                    log.error(f"\n[ERRO CRÍTICO NO TELEFONE {tel_fmt}] {e}")
                    log.warning(f"O telefone {tel_fmt} NÃO foi marcado como consultado.")
                    log.warning("Encerrando o programa imediatamente.")
                    break

                # Salva checkpoint a cada 5 consultas
                if idx % 5 == 0:
                    novos_trecho = novos_resultados[ultimo_salvo:]
                    salvar_novos_resultados(novos_trecho, ARQUIVO_SAIDA)
                    ultimo_salvo = len(novos_resultados)
                    log.info(f"  ✔ Checkpoint: {ultimo_salvo} registro(s) salvos no arquivo '{ARQUIVO_SAIDA}'")

                # Intervalo obrigatório de 10 segundos entre pesquisas
                if idx < total:
                    log.info(f"  ⏳ Aguardando {TEMPO_ENTRE_BUSCAS}s para próxima consulta...")
                    time.sleep(TEMPO_ENTRE_BUSCAS)

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
    log.info(f"  CONCLUÍDO — {len(novos_resultados)} registro(s) obtidos")
    log.info(f"  Arquivo de saída gerado: {ARQUIVO_SAIDA}")
    log.info(f"  Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
