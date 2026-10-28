import csv
import logging
from pathlib import Path
from typing import List, Set

from core.config import AppConfig
from core.domain import ResultadoClienteFiltro, formatar_cpf, limpar_cpf

log = logging.getLogger(__name__)


class CpfFilterRepository:
    """
    Repositório de dados para leitura de CPFs e persistência de clientes filtrados.
    Isola todas as operações de persistência em CSV e arquivo de histórico.
    """

    CAMPOS_SAIDA = ["CPF", "Idade", "Tel 1", "Tel 2", "Parcelas Pagas"]

    def __init__(self, config: AppConfig):
        self.config = config
        self.arquivo_entrada = config.resolver_arquivo_entrada()
        self.arquivo_saida = Path(config.arquivo_saida)
        self.arquivo_historico = Path(config.arquivo_historico)

    def inicializar(self) -> None:
        """Garante a existência e validação dos arquivos necessários."""
        if not self.arquivo_entrada.exists():
            raise FileNotFoundError(
                f"Arquivo de entrada não encontrado: '{self.arquivo_entrada}'. "
                f"Verifique a variável ARQUIVO_ENTRADA no arquivo .env."
            )

        if not self.arquivo_saida.exists():
            with open(self.arquivo_saida, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(
                    f, fieldnames=self.CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL
                )
                writer.writeheader()
            log.info(f"Arquivo de saída criado: '{self.arquivo_saida.name}'")

        if not self.arquivo_historico.exists():
            self.arquivo_historico.touch()

    def carregar_cpfs_entrada(self) -> List[str]:
        """Lê os CPFs válidos da primeira coluna do arquivo de entrada (CSV ou Excel)."""
        if not self.arquivo_entrada.exists():
            raise FileNotFoundError(f"Arquivo de entrada não encontrado: '{self.arquivo_entrada}'")

        cpfs_brutos: List[str] = []
        ext = self.arquivo_entrada.suffix.lower()

        if ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(self.arquivo_entrada, read_only=True, data_only=True)
            ws = wb.active
            if ws is not None:
                for row in ws.iter_rows(min_row=1, values_only=True):
                    if row and row[0] is not None:
                        val = str(row[0]).strip()
                        if val:
                            cpfs_brutos.append(val)
            wb.close()
        else:
            with open(self.arquivo_entrada, "r", encoding="utf-8-sig", errors="replace") as f:
                primeira_linha = f.readline()
                delimitador = ";" if ";" in primeira_linha else ","
                f.seek(0)

                leitor = csv.reader(f, delimiter=delimitador)
                for linha in leitor:
                    if linha and linha[0].strip():
                        val = linha[0].strip()
                        if val.lower() == "cpf":
                            continue
                        cpfs_brutos.append(val)

        vistos: Set[str] = set()
        cpfs_unicos: List[str] = []
        for c in cpfs_brutos:
            c_limpo = limpar_cpf(c)
            if len(c_limpo) == 11 and c_limpo not in vistos:
                vistos.add(c_limpo)
                cpfs_unicos.append(c_limpo)

        log.info(f"{len(cpfs_unicos)} CPF(s) válido(s) carregado(s) de '{self.arquivo_entrada.name}'")
        return cpfs_unicos

    def carregar_cpfs_processados(self) -> Set[str]:
        """Retorna conjunto de CPFs já processados (lendo tanto resultados.csv quanto o histórico)."""
        processados: Set[str] = set()

        # 1. Lê os CPFs aprovados já salvos no arquivo de saída
        if self.arquivo_saida.exists():
            try:
                with open(self.arquivo_saida, "r", encoding="utf-8-sig", errors="replace") as f:
                    primeira_linha = f.readline()
                    delimitador = ";" if ";" in primeira_linha else ","
                    f.seek(0)

                    reader = csv.DictReader(f, delimiter=delimitador)
                    for row in reader:
                        cpf = limpar_cpf(row.get("CPF", ""))
                        if cpf:
                            processados.add(cpf)
            except Exception as e:
                log.warning(f"Aviso ao ler arquivo de saída existente: {e}")

        # 2. Lê os CPFs do histórico geral (inclusive os reprovados pelos filtros)
        if self.arquivo_historico.exists():
            try:
                with open(self.arquivo_historico, "r", encoding="utf-8", errors="replace") as f:
                    for linha in f:
                        cpf = limpar_cpf(linha.strip())
                        if cpf:
                            processados.add(cpf)
            except Exception as e:
                log.warning(f"Aviso ao ler histórico geral: {e}")

        if processados:
            log.info(f"{len(processados)} CPF(s) já consultados anteriormente — serão pulados.")

        return processados

    def obter_pendentes(self) -> List[str]:
        """Retorna a lista de CPFs pendentes para consulta nesta sessão."""
        todos = self.carregar_cpfs_entrada()
        processados = self.carregar_cpfs_processados()

        pendentes = [c for c in todos if c not in processados]

        if len(pendentes) > self.config.limite_cpfs:
            log.info(
                f"{len(pendentes)} CPF(s) pendentes — limitando a {self.config.limite_cpfs} nesta execução."
            )
            pendentes = pendentes[: self.config.limite_cpfs]

        return pendentes

    def marcar_como_consultado(self, cpf_raw: str) -> None:
        """Registra o CPF consultado no arquivo de histórico para não reprocessar."""
        cpf_limpo = limpar_cpf(cpf_raw)
        if not cpf_limpo:
            return
        try:
            with open(self.arquivo_historico, "a", encoding="utf-8") as f:
                f.write(f"{cpf_limpo}\n")
        except Exception as e:
            log.debug(f"Aviso ao registrar CPF no histórico: {e}")

    def salvar_resultados(self, novos: List[ResultadoClienteFiltro]) -> None:
        """Salva novos registros aprovados no arquivo CSV de saída."""
        if not novos:
            return

        arquivo_existe = self.arquivo_saida.exists() and self.arquivo_saida.stat().st_size > 0

        with open(self.arquivo_saida, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=self.CAMPOS_SAIDA,
                delimiter=";",
                quoting=csv.QUOTE_MINIMAL,
            )
            if not arquivo_existe:
                writer.writeheader()
            for r in novos:
                writer.writerow(r.para_dicionario())

        log.info(f"{len(novos)} novo(s) registro(s) salvo(s) em '{self.arquivo_saida.name}'")
