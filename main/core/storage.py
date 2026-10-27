import csv
import logging
import os
from pathlib import Path

from core.config import AppConfig
from core.domain import (
    ResultadoConsulta,
    formatar_telefone,
    limpar_telefone,
)

log = logging.getLogger(__name__)


class PhoneRepository:
    """Gerencia a persistência de dados de entrada, saída e histórico."""

    CAMPOS_SAIDA = ["Telefone", "CPF"]

    def __init__(self, config: AppConfig):
        self.config = config
        self.arquivo_entrada = config.resolver_arquivo_entrada()
        self.arquivo_saida = Path(config.arquivo_saida)
        self.arquivo_historico = Path(config.arquivo_historico)

    def inicializar(self) -> None:
        """Garante que o arquivo de saída exista com o cabeçalho correto em 2 colunas."""
        if not self.arquivo_saida.exists():
            with open(self.arquivo_saida, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=self.CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
                writer.writeheader()
            log.info(f"Arquivo de saída '{self.arquivo_saida}' criado com cabeçalho {self.CAMPOS_SAIDA}.")

    def carregar_telefones_entrada(self) -> list[str]:
        """Lê os telefones válidos da primeira coluna do arquivo de entrada."""
        if not self.arquivo_entrada.exists():
            raise FileNotFoundError(
                f"Arquivo de entrada não encontrado: '{self.config.arquivo_entrada}'"
            )

        tels: list[str] = []
        ext = self.arquivo_entrada.suffix.lower()

        if ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(self.arquivo_entrada, read_only=True, data_only=True)
            ws = wb.active
            if ws is not None:
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if row and row[0] is not None:
                        val = str(row[0]).strip()
                        if val:
                            tels.append(val)
            wb.close()
        else:
            with open(self.arquivo_entrada, "r", encoding="utf-8-sig", errors="replace") as f:
                primeira_linha = f.readline()
                delimitador = ";" if ";" in primeira_linha else ","
                f.seek(0)

                leitor = csv.reader(f, delimiter=delimitador)
                next(leitor, None)  # Pula o cabeçalho
                for linha in leitor:
                    if linha and linha[0].strip():
                        tels.append(linha[0].strip())

        # Deduplicação mantendo ordem original
        vistos = set()
        unicos: list[str] = []
        for t in tels:
            t_limpo = limpar_telefone(t)
            if len(t_limpo) in (10, 11) and t_limpo not in vistos:
                vistos.add(t_limpo)
                unicos.append(t)

        log.info(f"{len(unicos)} telefone(s) válido(s) carregado(s) de '{self.arquivo_entrada.name}'")
        return unicos

    def carregar_telefones_processados(self) -> set[str]:
        """Retorna conjunto de telefones já consultados (lendo CSV de saída e histórico TXT)."""
        processados: set[str] = set()

        # Lê arquivo de saída existente
        if self.arquivo_saida.exists():
            try:
                with open(self.arquivo_saida, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        t = limpar_telefone(row.get("Telefone", ""))
                        if t:
                            processados.add(t)
            except Exception as e:
                log.warning(f"Aviso ao ler arquivo de saída: {e}")

        # Lê histórico geral TXT
        if self.arquivo_historico.exists():
            try:
                with open(self.arquivo_historico, "r", encoding="utf-8") as f:
                    for linha in f:
                        t = limpar_telefone(linha.strip())
                        if t:
                            processados.add(t)
            except Exception as e:
                log.warning(f"Aviso ao ler histórico: {e}")

        if processados:
            log.info(f"{len(processados)} telefone(s) já consultados anteriormente — serão pulados.")
        return processados

    def obter_pendentes(self) -> list[str]:
        """Retorna a fila de telefones pendentes respeitando o limite configurado."""
        todos = self.carregar_telefones_entrada()
        ja_feitos = self.carregar_telefones_processados()
        pendentes = [t for t in todos if limpar_telefone(t) not in ja_feitos]

        if len(pendentes) > self.config.limite_registros:
            log.info(
                f"{len(pendentes)} telefone(s) pendentes — limitando a {self.config.limite_registros} nesta execução."
            )
            return pendentes[: self.config.limite_registros]

        return pendentes

    def salvar_resultados(self, novos: list[ResultadoConsulta]) -> None:
        """Salva uma lista de novos resultados no arquivo CSV de saída."""
        if not novos:
            return

        self.inicializar()
        with open(self.arquivo_saida, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.CAMPOS_SAIDA, quoting=csv.QUOTE_MINIMAL)
            for item in novos:
                writer.writerow(item.to_dict())

        log.info(f"{len(novos)} novo(s) registro(s) adicionado(s) em '{self.arquivo_saida.name}'")

    def marcar_como_consultado(self, telefone: str) -> None:
        """Registra o telefone no arquivo de histórico para evitar reconsultas."""
        t_limpo = limpar_telefone(telefone)
        if not t_limpo:
            return
        try:
            with open(self.arquivo_historico, "a", encoding="utf-8") as f:
                f.write(f"{t_limpo}\n")
        except Exception as e:
            log.debug(f"Aviso ao salvar no histórico: {e}")
