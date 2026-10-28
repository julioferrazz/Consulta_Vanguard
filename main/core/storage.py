import csv
import logging
from pathlib import Path
from typing import List, Set

from core.config import AppConfig
from core.domain import ResultadoMargem, formatar_cpf, limpar_cpf

log = logging.getLogger(__name__)


class CpfMargemRepository:
    """
    Repositório de dados para leitura da base de CPFs e persistência dos resultados.
    Isola todas as operações de I/O em disco do restante da aplicação.
    """

    CAMPOS_SAIDA = ["CPF", "Banco", "Margem_Disponivel"]

    def __init__(self, config: AppConfig):
        self.config = config
        self.arquivo_entrada = config.resolver_arquivo_entrada()
        self.arquivo_saida = Path(config.arquivo_saida)

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

    def carregar_cpfs_entrada(self) -> List[str]:
        """Lê os CPFs da primeira coluna do arquivo de entrada (CSV ou Excel)."""
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
                        # Se for o cabeçalho 'cpf', pula
                        if val.lower() == "cpf":
                            continue
                        cpfs_brutos.append(val)

        # Remove duplicatas preservando a ordem original
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
        """Lê os CPFs já salvos no arquivo de saída para evitar reprocessamento."""
        processados: Set[str] = set()
        if not self.arquivo_saida.exists():
            return processados

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

            if processados:
                log.info(f"{len(processados)} CPF(s) já presentes em '{self.arquivo_saida.name}' — serão pulados.")
        except Exception as e:
            log.warning(f"Aviso ao ler histórico de resultados: {e}")

        return processados

    def obter_pendentes(self) -> List[str]:
        """Retorna a lista de CPFs pendentes para consulta na sessão atual."""
        todos = self.carregar_cpfs_entrada()
        processados = self.carregar_cpfs_processados()

        pendentes = [c for c in todos if c not in processados]

        if len(pendentes) > self.config.limite_cpfs:
            log.info(
                f"{len(pendentes)} CPF(s) pendentes — limitando a {self.config.limite_cpfs} nesta execução."
            )
            pendentes = pendentes[: self.config.limite_cpfs]

        return pendentes

    def salvar_resultados(self, novos: List[ResultadoMargem]) -> None:
        """Salva um lote de novos resultados no arquivo CSV de saída."""
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
