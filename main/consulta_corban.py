#!/usr/bin/env python3
"""
============================================================
  CONSULTA CORBAN - Automação de Consultas e Filtros
============================================================
  Automatiza a consulta de clientes por CPF no
  sistema gestao.sistemacorban.com.br

  Filtros aplicados por benefício:
  - Idade: Menos de 80 anos (< 80)
  - Telefones: Desanonimiza e extrai Tel 1 e Tel 2
  - Empréstimo: Último contrato com até 10 parcelas pagas (<= 10)

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
# ✏️  CONFIGURAÇÕES — Edite estas variáveis se necessário
# ============================================================

USUARIO = "Supervisao@37277"  # ← seu usuário de login
SENHA = "Safira@26"  # ← sua senha de login
ARQUIVO_ENTRADA = "cpfs.csv"  # ← arquivo com CPFs na 1ª coluna
ARQUIVO_SAIDA = "resultados.csv"  # ← arquivo de saída (gerado)
ARQUIVO_HISTORICO = "cpfs_consultados.txt"  # ← histórico de todos os CPFs já consultados
MODO_VISIVEL = True  # True = mostra o navegador
TEMPO_ESPERA = 2  # Segundos de pausa entre ações
LIMITE_CPFS = 500  # Máximo de CPFs pendentes a processar por execução

# ── Regras de Filtro e Limite de Tempo ─────────────────────────
IDADE_LIMITE = 81  # Apenas pessoas com menos de 81 anos (< 81)
PARCELAS_PAGAS_LIMITE = 10  # Apenas até 10 parcelas pagas (<= 10)
PULAR_SE_SEM_EMPRESTIMO = True  # Pular se não houver empréstimo averbado
TIMEOUT_BUSCA = 25  # Segundos limite esperando a resposta antes de encerrar por limite diário


class LimiteConsultasExcedido(Exception):
    """Lançada quando a busca excede 25s, indicando limite diário de consultas atingido."""
    pass


# ── Colunas do Arquivo de Saída ───────────────────────────────
CAMPOS_SAIDA = ["CPF", "Idade", "Telefone_1", "Telefone_2", "Parcelas_Pagas"]

# ============================================================

SITE_URL = "https://gestao.sistemacorban.com.br"

# ── Logging & Encodings ───────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────
#  Utilitários & Filtros de Limpeza
# ─────────────────────────────────────────────────────────────


def formatar_telefone(tel: str) -> str:
    """Padroniza o telefone no formato (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    if not tel or tel == "N/A":
        return "N/A"
    tel = tel.strip()
    if "(" in tel and ")" in tel and "-" in tel:
        return tel
    digitos = "".join(filter(str.isdigit, tel))
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    elif len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return tel if tel else "N/A"


def calcular_idade(texto: str) -> int | None:
    """Calcula a idade a partir da data de nascimento (DD/MM/AAAA) ou texto direto."""
    if not texto or texto == "N/A":
        return None
    texto = texto.strip()

    # Formato com data completa: DD/MM/AAAA ou DD-MM-AAAA
    m_data = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", texto)
    if m_data:
        try:
            dia = int(m_data.group(1))
            mes = int(m_data.group(2))
            ano = int(m_data.group(3))
            nasc = datetime(ano, mes, dia)
            hoje = datetime.now()
            idade = hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
            return idade
        except Exception:
            pass

    # Formato textual direto (ex: "74 anos")
    m_anos = re.search(r"(\d{1,3})\s*anos?", texto, re.IGNORECASE)
    if m_anos:
        return int(m_anos.group(1))

    # Apenas o número inteiro da idade
    m_num = re.search(r"^\d{1,3}$", texto)
    if m_num:
        val = int(m_num.group(0))
        if 0 <= val <= 120:
            return val

    return None


def ler_cpfs(arquivo: str) -> list[str]:
    """Lê CPFs da primeira coluna de um arquivo CSV ou Excel (.xlsx), pulando sempre a primeira linha (cabeçalho)."""
    cpfs = []
    ext = Path(arquivo).suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
            ws = wb.active
            # min_row=2 garante que a primeira linha (cabeçalho) seja sempre pulada
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and row[0] is not None:
                    val = str(row[0]).strip()
                    if val:
                        cpfs.append(val)
            wb.close()
        else:  # CSV / TXT
            with open(arquivo, "r", encoding="utf-8-sig") as f:
                leitor = csv.reader(f)
                # Pula sempre a primeira linha (cabeçalho)
                next(leitor, None)
                for linha in leitor:
                    if linha and linha[0].strip():
                        cpfs.append(linha[0].strip())
    except Exception as e:
        log.error(f"Erro ao ler o arquivo de entrada: {e}")
        sys.exit(1)

    # Remove duplicatas e garante apenas CPFs válidos com 11 dígitos numéricos
    vistos = set()
    unicos = []
    for c in cpfs:
        cpf_limpo = limpar_cpf(c)
        if len(cpf_limpo) == 11 and cpf_limpo not in vistos:
            vistos.add(cpf_limpo)
            unicos.append(c)

    log.info(f"{len(unicos)} CPF(s) válido(s) carregado(s) de '{arquivo}' (cabeçalho pulado com sucesso)")
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


def registrar_cpf_consultado(cpf: str, arquivo: str = ARQUIVO_HISTORICO) -> None:
    """Registra o CPF no histórico de consultados para garantir que não seja consultado novamente."""
    cpf_limpo = limpar_cpf(cpf)
    if not cpf_limpo:
        return
    try:
        with open(arquivo, "a", encoding="utf-8") as f:
            f.write(f"{cpf_limpo}\n")
    except Exception as e:
        log.debug(f"Aviso ao registrar CPF no histórico: {e}")


def ler_cpfs_processados(arquivo_saida: str = ARQUIVO_SAIDA, arquivo_historico: str = ARQUIVO_HISTORICO) -> set[str]:
    """Retorna conjunto de CPFs já processados (lendo tanto resultados.csv quanto o histórico)."""
    processados: set[str] = set()

    # 1. Lê os CPFs aprovados já salvos no arquivo de saída
    if os.path.exists(arquivo_saida):
        try:
            with open(arquivo_saida, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cpf = limpar_cpf(row.get("CPF", ""))
                    if cpf:
                        processados.add(cpf)
        except Exception as e:
            log.warning(f"Não foi possível ler o arquivo de saída existente: {e}")

    # 2. Lê os CPFs do histórico geral (inclusive os que foram descartados pelos filtros)
    if os.path.exists(arquivo_historico):
        try:
            with open(arquivo_historico, "r", encoding="utf-8") as f:
                for linha in f:
                    cpf = limpar_cpf(linha.strip())
                    if cpf:
                        processados.add(cpf)
        except Exception as e:
            log.warning(f"Não foi possível ler o arquivo de histórico: {e}")

    if processados:
        log.info(
            f"{len(processados)} CPF(s) já consultados anteriormente — serão pulados"
        )
    return processados


def inicializar_arquivo_saida(arquivo: str) -> None:
    """Garante que o arquivo de saída exista com o cabeçalho correto desde o início."""
    if not os.path.exists(arquivo):
        with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
        log.info(f"Arquivo de saída '{arquivo}' criado com cabeçalho.")
        return

    # Se já existir, verifica se o cabeçalho é do padrão anterior
    try:
        cabecalho = ""
        with open(arquivo, "r", encoding="utf-8-sig") as f:
            cabecalho = f.readline().strip()

        if cabecalho and "Margem_Disponivel" in cabecalho:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_nome = f"resultados_antigo_margem_{ts}.csv"
            os.rename(arquivo, backup_nome)
            log.info(
                f"Detectado arquivo de resultados antigo com outras colunas. "
                f"Salvo backup em '{backup_nome}'."
            )
            with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
                writer.writeheader()
            log.info(f"Novo arquivo de saída '{arquivo}' criado.")
    except Exception as e:
        log.warning(f"Aviso ao verificar arquivo existente: {e}")


def salvar_novos_resultados(novos: list[dict], arquivo: str) -> None:
    """Adiciona novos registros ao arquivo CSV. Garante que o arquivo exista mesmo sem novos registros."""
    inicializar_arquivo_saida(arquivo)

    if not novos:
        return

    with open(arquivo, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
        writer.writerows(novos)

    log.info(f"{len(novos)} novo(s) registro(s) adicionado(s) em '{arquivo}'")


# ─────────────────────────────────────────────────────────────
#  Ações de navegação e extração
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
    """Garante que o filtro esteja em CPF e dispara a busca."""
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


def desanonimizar_telefones(page) -> None:
    """Clica no botão de desanonimização de telefones para exibir os números reais."""
    try:
        btn = page.query_selector("#btnHideOrShowTelefone")
        if btn and btn.is_visible():
            btn.click()
            page.wait_for_timeout(800)
        else:
            # Fallback caso o botão não esteja visível ou precise chamar a função direta
            page.evaluate("""() => {
                if (typeof abrirCamposTelefones === 'function') {
                    abrirCamposTelefones();
                }
            }""")
            page.wait_for_timeout(500)
    except Exception as e:
        log.debug(f"Aviso ao liberar telefones: {e}")


def extrair_e_filtrar_beneficio(page) -> dict | None:
    """
    Extrai as informações da página detalhada do benefício e aplica os filtros:
    1. Idade (< IDADE_LIMITE anos)
    2. Parcelas pagas no último empréstimo (<= PARCELAS_PAGAS_LIMITE)
    3. Telefones 1 e 2

    Retorna um dicionário com os dados caso aprovado em ambos os filtros, ou None se reprovado.
    """
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
        log.warning(f"     ⚠ Não foi possível determinar a idade (valor lido: '{nasc_raw}'). Pulando benefício.")
        return None

    if idade >= IDADE_LIMITE:
        log.info(f"     [Filtro Reprovado] Idade {idade} anos >= limite de {IDADE_LIMITE} anos. Pulando.")
        return None

    log.info(f"     [Filtro OK] Idade: {idade} anos (< {IDADE_LIMITE})")

    # ── 2. Extração das parcelas pagas no último empréstimo ────
    pagas = None
    try:
        res_pagas = page.evaluate("""() => {
            // Tenta obter do input oculto da primeira linha de contrato (#titlelinha_0)
            const inp = document.querySelector('#linhaBanco--pagas_0');
            if (inp && inp.value !== '') {
                const num = parseInt(inp.value.trim(), 10);
                if (!isNaN(num)) return num;
            }

            // Tenta obter do texto 'XX Pagas' no primeiro container de contrato
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
        if PULAR_SE_SEM_EMPRESTIMO:
            log.info("     [Filtro Reprovado] Nenhum empréstimo ativo encontrado no benefício. Pulando.")
            return None
        pagas = 0

    if pagas > PARCELAS_PAGAS_LIMITE:
        log.info(
            f"     [Filtro Reprovado] {pagas} parcelas pagas no último empréstimo "
            f"(limite é até {PARCELAS_PAGAS_LIMITE} pagas). Pulando."
        )
        return None

    log.info(f"     [Filtro OK] Parcelas Pagas: {pagas} (<= {PARCELAS_PAGAS_LIMITE})")

    # ── 3. Extração dos dois telefones ─────────────────────────
    desanonimizar_telefones(page)

    tel1_raw = "N/A"
    tel2_raw = "N/A"

    try:
        tels = page.evaluate("""() => {
            let t1 = '';
            let t2 = '';

            // Tel 1
            const divTel1 = document.querySelector('[data-telefonehot="1"]');
            const inpTel1 = document.querySelector('#telefonesHot-1') || document.querySelector('#telhot-1');
            if (divTel1 && divTel1.innerText.trim() && !divTel1.innerText.includes('*')) {
                t1 = divTel1.innerText.trim();
            } else if (inpTel1 && inpTel1.value.trim()) {
                t1 = inpTel1.value.trim();
            } else if (divTel1) {
                t1 = divTel1.innerText.trim();
            }

            // Tel 2
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
            tel1_raw = tels.get("t1") or "N/A"
            tel2_raw = tels.get("t2") or "N/A"
    except Exception as e:
        log.debug(f"Erro ao extrair telefones: {e}")

    tel1 = formatar_telefone(tel1_raw)
    tel2 = formatar_telefone(tel2_raw)

    return {
        "Idade": idade,
        "Telefone_1": tel1,
        "Telefone_2": tel2,
        "Parcelas_Pagas": pagas,
    }


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

        # Aguarda a resposta da busca por até TIMEOUT_BUSCA (25s)
        # Seletor contempla botões de benefício OU avisos/modais rápidos de cliente sem benefício
        seletor_resposta = ".selecionar--beneficio__btn, .swal2-popup, .toast, .alert, #swal2-title, .sweet-alert"
        try:
            page.wait_for_selector(seletor_resposta, timeout=TIMEOUT_BUSCA * 1_000)
        except PlaywrightTimeout:
            raise LimiteConsultasExcedido(
                f"Tempo de {TIMEOUT_BUSCA}s excedido aguardando resposta para o CPF {cpf_fmt}. "
                f"Provável limite diário de consultas atingido no sistema Corban!"
            )

        botoes = page.query_selector_all(".selecionar--beneficio__btn")
        qtd = len(botoes)
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

                # Extrai dados e avalia os filtros
                dados = extrair_e_filtrar_beneficio(page)

                if dados is not None:
                    resultados.append(
                        {
                            "CPF": cpf_fmt,
                            "Idade": dados["Idade"],
                            "Telefone_1": dados["Telefone_1"],
                            "Telefone_2": dados["Telefone_2"],
                            "Parcelas_Pagas": dados["Parcelas_Pagas"],
                        }
                    )
                    log.info(
                        f"     ★ REGISTRO APROVADO: CPF={cpf_fmt} | Idade={dados['Idade']} | "
                        f"Tel1={dados['Telefone_1']} | Tel2={dados['Telefone_2']} | "
                        f"Pagas={dados['Parcelas_Pagas']}"
                    )
                else:
                    log.info(f"     ⏭ Benefício {i+1} descartado pelos critérios de filtro.")

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

    except LimiteConsultasExcedido:
        raise
    except Exception as e:
        log.error(f"  Erro ao processar CPF {cpf_fmt}: {e}")


# ─────────────────────────────────────────────────────────────
#  Ponto de entrada
# ─────────────────────────────────────────────────────────────


def main() -> None:
    log.info("=" * 60)
    log.info("  CONSULTA CORBAN - FILTRO IDADE & EMPRÉSTIMOS")
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

    inicializar_arquivo_saida(ARQUIVO_SAIDA)
    ja_processados = ler_cpfs_processados(ARQUIVO_SAIDA, ARQUIVO_HISTORICO)
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
                try:
                    processar_cpf(pagina, cpf, novos_resultados)
                    registrar_cpf_consultado(cpf)
                except LimiteConsultasExcedido as e:
                    log.error(f"\n[LIMITE DIÁRIO ATINGIDO] {e}")
                    log.warning(
                        "Encerrando o script imediatamente para preservar os CPFs pendentes para o próximo dia."
                    )
                    break

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
