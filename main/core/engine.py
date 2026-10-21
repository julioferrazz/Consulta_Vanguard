import logging
import time
from datetime import datetime

from core.config import AppConfig
from core.corban_client import CorbanClient
from core.domain import (
    ErroParadaImediata,
    LimiteConsultasExcedido,
    ResultadoConsulta,
    formatar_telefone,
    limpar_telefone,
)
from core.storage import PhoneRepository

log = logging.getLogger(__name__)


class ConsultaEngine:
    """
    Orquestrador do fluxo de consultas com suporte a rotação automática de contas
    quando o limite diário de consultas for atingido e verificação estrita de duplicatas.
    """

    def __init__(self, config: AppConfig, repository: PhoneRepository, client: CorbanClient):
        self.config = config
        self.repository = repository
        self.client = client
        self.indice_conta_atual = 0

    def executar(self) -> None:
        """Executa o ciclo completo de consultas para a fila de telefones pendentes."""
        log.info("=" * 60)
        log.info("  INICIANDO CONSULTA CORBAN (TELEFONE -> CPF)")
        log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info(f"  Contas disponíveis: {len(self.config.contas)}")
        log.info(f"  Conta inicial ativa: '{self.client.credencial_ativa.usuario}'")
        log.info("=" * 60)

        # Carrega histórico global de números já processados (CSV de saída + TXT de histórico)
        ja_processados = self.repository.carregar_telefones_processados()
        consultados_na_sessao: set[str] = set()

        pendentes = self.repository.obter_pendentes()
        total = len(pendentes)

        if total == 0:
            log.info("Nenhum telefone pendente para consulta. Todos já foram verificados!")
            return

        log.info(f"{total} telefone(s) pendente(s) para processar nesta sessão.")

        novos_resultados: list[ResultadoConsulta] = []
        ultimo_salvo = 0
        idx_tel = 0

        try:
            while idx_tel < total:
                tel_raw = pendentes[idx_tel]
                tel_fmt = formatar_telefone(tel_raw)
                tel_limpo = limpar_telefone(tel_raw)

                # 1. Validação de formato
                if len(tel_limpo) not in (10, 11):
                    log.warning(f"[{idx_tel + 1}/{total}] Telefone ignorado (formato inválido): {tel_raw!r}")
                    idx_tel += 1
                    continue

                # 2. Verificação se o número já foi verificado anteriormente ou nesta sessão
                if tel_limpo in ja_processados or tel_limpo in consultados_na_sessao:
                    log.info(f"[{idx_tel + 1}/{total}] Telefone {tel_fmt} já foi verificado anteriormente. Pulando.")
                    idx_tel += 1
                    continue

                conta_atual = self.client.credencial_ativa.usuario
                log.info(f"\n[{idx_tel + 1}/{total}] Pesquisando: {tel_fmt} (Conta: {conta_atual})")

                try:
                    cpfs = self.client.buscar_cpfs_por_telefone(tel_raw)

                    if not cpfs:
                        log.info(f"  [-] Nenhum CPF associado ao telefone {tel_fmt}")
                    else:
                        for cpf in cpfs:
                            item = ResultadoConsulta(telefone=tel_fmt, cpf=cpf)
                            novos_resultados.append(item)
                            log.info(f"  [★ ENCONTRADO] Telefone: {tel_fmt} | CPF: {cpf}")

                    # Marca como consultado tanto no disco quanto em memória
                    self.repository.marcar_como_consultado(tel_raw)
                    ja_processados.add(tel_limpo)
                    consultados_na_sessao.add(tel_limpo)
                    idx_tel += 1

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
                            log.info(f"✔ Nova conta pronta! Retestando o telefone {tel_fmt}...")
                            time.sleep(2)
                            # Não incrementa idx_tel: repete a busca do mesmo telefone na nova conta
                            continue
                        except Exception as err_troca:
                            log.error(f"Erro ao alternar para conta '{proxima_conta.usuario}': {err_troca}")
                            log.warning("Tentando avançar para a próxima conta disponível...")
                            continue
                    else:
                        log.error(
                            "\n[LIMITE TOTAL ATINGIDO] Todas as contas disponíveis atingiram o limite diário de consultas!"
                        )
                        log.warning(
                            f"O telefone {tel_fmt} e os seguintes permanecerão na lista de pendentes para o próximo dia."
                        )
                        log.warning("Encerrando a execução para preservar os dados pendentes.")
                        break

                except ErroParadaImediata as e:
                    log.error(f"\n[ERRO DE EXECUÇÃO DETECTADO] {e}")
                    log.warning(f"O telefone {tel_fmt} NÃO foi marcado como consultado para nova tentativa futura.")
                    log.warning("Encerrando o processo com segurança.")
                    break

                except Exception as e:
                    log.error(f"\n[ERRO INESPERADO NO TELEFONE {tel_fmt}] {e}")
                    log.warning(f"O telefone {tel_fmt} NÃO foi marcado como consultado.")
                    log.warning("Encerrando o processo imediatamente.")
                    break

                # Checkpoint periódico a cada N registros salvos
                if idx_tel > 0 and idx_tel % self.config.intervalo_checkpoint == 0:
                    trecho = novos_resultados[ultimo_salvo:]
                    if trecho:
                        self.repository.salvar_resultados(trecho)
                        ultimo_salvo = len(novos_resultados)
                        log.info(f"  ✔ Checkpoint: {ultimo_salvo} registro(s) salvos no CSV.")

                # Intervalo obrigatório de 10 segundos entre consultas no Corban
                if idx_tel < total:
                    log.info(f"  ⏳ Aguardando {self.config.tempo_entre_buscas}s para próxima consulta...")
                    time.sleep(self.config.tempo_entre_buscas)

        except KeyboardInterrupt:
            log.info("\nExecução interrompida pelo usuário (Ctrl+C).")
        finally:
            restantes = novos_resultados[ultimo_salvo:]
            if restantes:
                self.repository.salvar_resultados(restantes)
                log.info(f"  ✔ Salvos {len(restantes)} registro(s) finais no CSV.")

        log.info("\n" + "=" * 60)
        log.info(f"  CONCLUÍDO — {len(novos_resultados)} registro(s) obtidos.")
        log.info(f"  Arquivo de saída: {self.repository.arquivo_saida.resolve()}")
        log.info(f"  Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info("=" * 60)
