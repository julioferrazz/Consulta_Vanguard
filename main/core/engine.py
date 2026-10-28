import logging
import time
from datetime import datetime
from typing import List, Set

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
    """
    Orquestrador do fluxo de consultas com aplicação de filtros de idade e empréstimo,
    suporte a rotação de contas por limite diário e salvamento de histórico.
    """

    def __init__(self, config: AppConfig, repository: CpfFilterRepository, client: CorbanClient):
        self.config = config
        self.repository = repository
        self.client = client
        self.indice_conta_atual = 0

    def executar(self) -> None:
        """Executa o ciclo completo de consultas e filtros para a fila de CPFs pendentes."""
        log.info("=" * 60)
        log.info("  INICIANDO CONSULTA CORBAN (FILTRO IDADE & EMPRÉSTIMO)")
        log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info(f"  Contas disponíveis: {len(self.config.contas)}")
        log.info(f"  Conta inicial ativa: '{self.client.credencial_ativa.usuario}'")
        log.info(f"  Filtros ativos: Idade < {self.config.idade_limite} | Pagas <= {self.config.parcelas_pagas_limite}")
        log.info("=" * 60)

        ja_processados = self.repository.carregar_cpfs_processados()
        consultados_na_sessao: Set[str] = set()

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
                    log.warning(f"[{idx + 1}/{total}] CPF ignorado (formato inválido): {cpf_raw!r}")
                    idx += 1
                    continue

                if cpf_limpo in ja_processados or cpf_limpo in consultados_na_sessao:
                    log.info(f"[{idx + 1}/{total}] CPF {cpf_fmt} já consultado anteriormente. Pulando.")
                    idx += 1
                    continue

                conta_atual = self.client.credencial_ativa.usuario
                log.info(f"\n[{idx + 1}/{total}] CPF: {cpf_fmt} (Conta: {conta_atual})")

                try:
                    aprovados = self.client.buscar_e_filtrar_por_cpf(cpf_raw)

                    if aprovados:
                        novos_resultados.extend(aprovados)

                    # Registra no histórico como consultado (mesmo se reprovado pelos filtros)
                    self.repository.marcar_como_consultado(cpf_raw)
                    ja_processados.add(cpf_limpo)
                    consultados_na_sessao.add(cpf_limpo)
                    idx += 1

                except LimiteConsultasExcedido as e:
                    log.warning(f"\n[LIMITE DIÁRIO ATINGIDO] {e}")
                    self.indice_conta_atual += 1

                    if self.indice_conta_atual < len(self.config.contas):
                        proxima_conta = self.config.contas[self.indice_conta_atual]
                        log.info(
                            f"🔄 Alternando para próxima conta "
                            f"[{self.indice_conta_atual + 1}/{len(self.config.contas)}]: '{proxima_conta.usuario}'..."
                        )
                        try:
                            self.client.alternar_conta(proxima_conta)
                            log.info(f"✔ Nova conta pronta! Retestando CPF {cpf_fmt}...")
                            time.sleep(2)
                            continue
                        except Exception as err_troca:
                            log.error(f"Erro ao alternar para conta '{proxima_conta.usuario}': {err_troca}")
                            continue
                    else:
                        log.error(
                            "\n[LIMITE TOTAL ATINGIDO] Todas as contas disponíveis atingiram o limite diário de consultas!"
                        )
                        log.warning(f"O CPF {cpf_fmt} e os seguintes permanecerão na lista de pendentes.")
                        break

                except ErroParadaImediata as e:
                    log.error(f"\n[ERRO DE EXECUÇÃO DETECTADO] {e}")
                    log.warning(f"O CPF {cpf_fmt} NÃO foi marcado como consultado para nova tentativa.")
                    break

                except Exception as e:
                    log.error(f"\n[ERRO INESPERADO NO CPF {cpf_fmt}] {e}")
                    log.warning(f"O CPF {cpf_fmt} NÃO foi marcado como consultado.")
                    break

                # Checkpoint periódico a cada N registros salvos
                if idx > 0 and idx % self.config.intervalo_checkpoint == 0:
                    trecho = novos_resultados[ultimo_salvo:]
                    if trecho:
                        self.repository.salvar_resultados(trecho)
                        ultimo_salvo = len(novos_resultados)
                        log.info(f"  ✔ Checkpoint: {ultimo_salvo} registro(s) salvos no CSV.")

        except KeyboardInterrupt:
            log.info("\nInterrompido pelo usuário (Ctrl+C).")
        finally:
            restantes = novos_resultados[ultimo_salvo:]
            if restantes:
                self.repository.salvar_resultados(restantes)
                log.info(f"  ✔ Salvos {len(restantes)} registro(s) finais no CSV.")

        log.info("\n" + "=" * 60)
        log.info(f"  CONCLUÍDO — {len(novos_resultados)} registro(s) obtidos.")
        log.info(f"  Arquivo de saída gerado: {self.repository.arquivo_saida.resolve()}")
        log.info(f"  Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info("=" * 60)
