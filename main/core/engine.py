import logging
import time
from datetime import datetime
from typing import List

from core.config import AppConfig
from core.corban_client import CorbanClient
from core.domain import (
    ErroParadaImediata,
    LimiteConsultasExcedido,
    ResultadoClienteFiltro,
    formatar_cpf,
    limpar_cpf,
)
from core.storage import CpfFilterRepository

log = logging.getLogger(__name__)


class ConsultaEngine:
    """Orquestrador do fluxo de consultas com aplicação de filtros, rotação de contas e checkpoints."""

    def __init__(self, config: AppConfig, repository: CpfFilterRepository, client: CorbanClient):
        self.config = config
        self.repository = repository
        self.client = client
        self.indice_conta_atual = 0

    def _rotacionar_conta(self) -> bool:
        """Tenta alternar para a próxima conta disponível ao atingir limite diário."""
        self.indice_conta_atual += 1
        if self.indice_conta_atual < len(self.config.contas):
            proxima = self.config.contas[self.indice_conta_atual]
            log.info(
                f"🔄 Alternando para próxima conta "
                f"[{self.indice_conta_atual + 1}/{len(self.config.contas)}]: '{proxima.usuario}'..."
            )
            try:
                self.client.alternar_conta(proxima)
                time.sleep(2)
                return True
            except Exception as e:
                log.error(f"Erro ao alternar para conta '{proxima.usuario}': {e}")
                return False

        log.error("\n[LIMITE TOTAL ATINGIDO] Todas as contas disponíveis atingiram o limite diário de consultas!")
        return False

    def executar(self) -> None:
        """Executa o ciclo completo de consultas e filtros para os CPFs pendentes."""
        log.info("=" * 60)
        log.info("  INICIANDO CONSULTA CORBAN (FILTRO IDADE & EMPRÉSTIMO)")
        log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info(f"  Contas disponíveis: {len(self.config.contas)} | Ativa: '{self.client.credencial_ativa.usuario}'")
        log.info(f"  Filtros: Idade < {self.config.idade_limite} | Pagas <= {self.config.parcelas_pagas_limite}")
        log.info("=" * 60)

        pendentes = self.repository.obter_pendentes()
        total = len(pendentes)
        if total == 0:
            log.info("Todos os CPFs do arquivo de entrada já foram consultados. Nada a fazer.")
            return

        log.info(f"{total} CPF(s) pendente(s) nesta execução.")
        novos_resultados: List[ResultadoClienteFiltro] = []
        ultimo_salvo = 0
        idx = 0

        try:
            while idx < total:
                cpf_raw = pendentes[idx]
                cpf_limpo = limpar_cpf(cpf_raw)
                cpf_fmt = formatar_cpf(cpf_limpo)

                if len(cpf_limpo) != 11:
                    log.warning(f"[{idx + 1}/{total}] CPF ignorado (inválido): {cpf_raw!r}")
                    idx += 1
                    continue

                log.info(f"\n[{idx + 1}/{total}] CPF: {cpf_fmt} (Conta: {self.client.credencial_ativa.usuario})")

                try:
                    if aprovados := self.client.buscar_e_filtrar_por_cpf(cpf_raw):
                        novos_resultados.extend(aprovados)

                    self.repository.marcar_como_consultado(cpf_raw)
                    idx += 1

                except LimiteConsultasExcedido as e:
                    log.warning(f"\n[LIMITE DIÁRIO ATINGIDO] {e}")
                    if self._rotacionar_conta():
                        log.info(f"✔ Nova conta pronta! Retestando CPF {cpf_fmt}...")
                        continue
                    log.warning(f"O CPF {cpf_fmt} e os seguintes permanecerão na lista de pendentes.")
                    break

                except ErroParadaImediata as e:
                    log.error(f"\n[ERRO DE EXECUÇÃO DETECTADO] {e}")
                    log.warning(f"O CPF {cpf_fmt} NÃO foi marcado como consultado para nova tentativa.")
                    break
                except Exception as e:
                    log.error(f"\n[ERRO INESPERADO NO CPF {cpf_fmt}] {e}")
                    break

                # Checkpoint periódico a cada N registros salvos
                if idx > 0 and idx % self.config.intervalo_checkpoint == 0:
                    if trecho := novos_resultados[ultimo_salvo:]:
                        self.repository.salvar_resultados(trecho)
                        ultimo_salvo = len(novos_resultados)
                        log.info(f"  ✔ Checkpoint: {ultimo_salvo} registro(s) salvos no CSV.")

        except KeyboardInterrupt:
            log.info("\nInterrompido pelo usuário (Ctrl+C).")
        finally:
            if restantes := novos_resultados[ultimo_salvo:]:
                self.repository.salvar_resultados(restantes)
                log.info(f"  ✔ Salvos {len(restantes)} registro(s) finais no CSV.")

        log.info("\n" + "=" * 60)
        log.info(f"  CONCLUÍDO — {len(novos_resultados)} registro(s) obtidos.")
        log.info(f"  Arquivo de saída gerado: {self.repository.arquivo_saida.resolve()}")
        log.info(f"  Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info("=" * 60)
