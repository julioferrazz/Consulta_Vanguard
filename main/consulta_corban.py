#!/usr/bin/env python3
"""
============================================================
  CONSULTA MARGEM CORBAN - Busca por CPF e Extração de Margem
============================================================
  Ponto de entrada (CLI) da aplicação.
  Orquestra o repositório de dados, o cliente de automação web
  e o motor de rotação de credenciais em arquitetura Deep Modules.

  Requisitos: playwright, openpyxl, python-dotenv
  Uso: python consulta_corban.py
============================================================
"""

import logging
import sys

from core.config import AppConfig
from core.corban_client import CorbanClient
from core.engine import ConsultaEngine
from core.storage import CpfMargemRepository

# ── Configuração de Logging ─────────────────────────────────
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        getattr(sys.stdout, "reconfigure")(encoding="utf-8", errors="backslashreplace")
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


def main() -> None:
    # Carrega configurações a partir do arquivo .env e variáveis de ambiente
    config = AppConfig.carregar()

    if not config.contas:
        log.error("Nenhuma conta configurada! Defina CORBAN_CONTAS no arquivo '.env'.")
        sys.exit(1)

    repository = CpfMargemRepository(config)

    try:
        repository.inicializar()
    except Exception as e:
        log.error(f"Erro ao inicializar arquivos: {e}")
        sys.exit(1)

    try:
        with CorbanClient(config) as client:
            engine = ConsultaEngine(config=config, repository=repository, client=client)
            engine.executar()
    except KeyboardInterrupt:
        log.info("Execução cancelada pelo usuário.")
    except Exception as e:
        log.exception(f"Erro fatal na execução: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
