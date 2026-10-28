import csv
import logging
from pathlib import Path
from typing import List, Set

from core.config import AppConfig
from core.domain import ResultadoClienteFiltro, limpar_cpf

log = logging.getLogger(__name__)


class CpfFilterRepository:
    """Repositório de dados para leitura de CPFs e persistência de clientes filtrados."""

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
                f"Defina ARQUIVO_ENTRADA no arquivo .env."
            )
        if not self.arquivo_saida.exists():
            with open(self.arquivo_saida, "w", newline="", encoding="utf-8-sig") as f:
                csv.DictWriter(f, fieldnames=self.CAMPOS_SAIDA, delimiter=";", quoting=csv.QUOTE_MINIMAL).writeheader()
            log.info(f"Arquivo de saída criado: '{self.arquivo_saida.name}'")

        if not self.arquivo_historico.exists():
            self.arquivo_historico.touch()

    def carregar_cpfs_entrada(self) -> List[str]:
        """Lê os CPFs válidos da primeira coluna do arquivo de entrada (CSV ou Excel)."""
        if not self.arquivo_entrada.exists():
            raise FileNotFoundError(f"Arquivo de entrada não encontrado: '{self.arquivo_entrada}'")

        brutos: List[str] = []
        if self.arquivo_entrada.suffix.lower() in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(self.arquivo_entrada, read_only=True, data_only=True)
            if ws := wb.active:
                brutos = [str(r[0]).strip() for r in ws.iter_rows(min_row=1, values_only=True) if r and r[0] is not None]
            wb.close()
        else:
            with open(self.arquivo_entrada, "r", encoding="utf-8-sig", errors="replace") as f:
                delimitador = ";" if ";" in f.readline() else ","
                f.seek(0)
                brutos = [r[0].strip() for r in csv.reader(f, delimiter=delimitador) if r and r[0].strip().lower() != "cpf"]

        cpfs: List[str] = []
        vistos: Set[str] = set()
        for c in brutos:
            if len(limpo := limpar_cpf(c)) == 11 and limpo not in vistos:
                vistos.add(limpo)
                cpfs.append(limpo)

        log.info(f"{len(cpfs)} CPF(s) válido(s) carregado(s) de '{self.arquivo_entrada.name}'")
        return cpfs

    def carregar_cpfs_processados(self) -> Set[str]:
        """Retorna conjunto de CPFs já processados (tanto no CSV de saída quanto no histórico)."""
        processados: Set[str] = set()

        if self.arquivo_saida.exists():
            try:
                with open(self.arquivo_saida, "r", encoding="utf-8-sig", errors="replace") as f:
                    delim = ";" if ";" in f.readline() else ","
                    f.seek(0)
                    processados.update(limpar_cpf(r.get("CPF", "")) for r in csv.DictReader(f, delimiter=delim) if r.get("CPF"))
            except Exception as e:
                log.warning(f"Aviso ao ler saída: {e}")

        if self.arquivo_historico.exists():
            try:
                with open(self.arquivo_historico, "r", encoding="utf-8", errors="replace") as f:
                    processados.update(limpar_cpf(l.strip()) for l in f if l.strip())
            except Exception as e:
                log.warning(f"Aviso ao ler histórico: {e}")

        processados.discard("")
        if processados:
            log.info(f"{len(processados)} CPF(s) já consultados anteriormente — serão pulados.")
        return processados

    def obter_pendentes(self) -> List[str]:
        """Retorna a lista de CPFs pendentes limitada pela configuração."""
        todos = self.carregar_cpfs_entrada()
        ja_feitos = self.carregar_cpfs_processados()
        pendentes = [c for c in todos if c not in ja_feitos]

        if len(pendentes) > self.config.limite_cpfs:
            log.info(f"{len(pendentes)} CPF(s) pendentes — limitando a {self.config.limite_cpfs} nesta execução.")
            pendentes = pendentes[: self.config.limite_cpfs]
        return pendentes

    def marcar_como_consultado(self, cpf_raw: str) -> None:
        """Registra o CPF no histórico para prevenir reprocessamento."""
        if limpo := limpar_cpf(cpf_raw):
            try:
                with open(self.arquivo_historico, "a", encoding="utf-8") as f:
                    f.write(f"{limpo}\n")
            except Exception as e:
                log.debug(f"Aviso ao registrar histórico: {e}")

    def salvar_resultados(self, novos: List[ResultadoClienteFiltro]) -> None:
        """Salva registros de clientes aprovados no CSV de saída."""
        if not novos:
            return
        novo_arq = not self.arquivo_saida.exists() or self.arquivo_saida.stat().st_size == 0
        with open(self.arquivo_saida, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.CAMPOS_SAIDA, delimiter=";", quoting=csv.QUOTE_MINIMAL)
            if novo_arq:
                writer.writeheader()
            writer.writerows(r.para_dicionario() for r in novos)
        log.info(f"{len(novos)} novo(s) registro(s) salvo(s) em '{self.arquivo_saida.name}'")
