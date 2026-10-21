import logging
import time
from datetime import datetime

from core.config import AppConfig
from core.corban_client import CorbanClient
from core.domain import (
    ErroParadaImediata,
    ResultadoConsulta,
    formatar_telefone,
    limpar_telefone,
)
from core.storage import PhoneRepository

log = logging.getLogger(__name__)


class ConsultaEngine:
    """
    Orquestrador do fluxo de consultas.
    Coordena a leitura de pendentes, chamadas ao cliente Corban, salvamento de checkpoints e pausas de 10s.
    """

    def __init__(self, config: AppConfig, repository: PhoneRepository, client: CorbanClient):
        self.config = config
        self.repository = repository
        self.client = client

    def executar(self) -> None:
        """Executa o ciclo completo de consultas para a fila de telefones pendentes."""
        log.info("=" * 60)
        log.info("  INICIANDO CONSULTA CORBAN (TELEFONE -> CPF)")
        log.info(f"  Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.info("=" * 60)

        pendentes = self.repository.obter_pendentes()
        total = len(pendentes)

        if total == 0:
            log.info("Nenhum telefone pendente para consulta. Tudo concluído!")
            return

        log.info(f"{total} telefone(s) pendente(s) para processar nesta sessão.")

        novos_resultados: list[ResultadoConsulta] = []
        ultimo_salvo = 0

        try:
            for idx, tel_raw in enumerate(pendentes, start=1):
                tel_fmt = formatar_telefone(tel_raw)
                tel_limpo = limpar_telefone(tel_raw)

                if len(tel_limpo) not in (10, 11):
                    log.warning(f"[{idx}/{total}] Telefone ignorado (formato inválido): {tel_raw!r}")
                    continue

                log.info(f"\n[{idx}/{total}] Pesquisando: {tel_fmt}")

                try:
                    cpfs = self.client.buscar_cpfs_por_telefone(tel_raw)

                    if not cpfs:
                        log.info(f"  [-] Nenhum CPF associado ao telefone {tel_fmt}")
                    else:
                        for cpf in cpfs:
                            item = ResultadoConsulta(telefone=tel_fmt, cpf=cpf)
                            novos_resultados.append(item)
                            log.info(f"  [★ ENCONTRADO] Telefone: {tel_fmt} | CPF: {cpf}")

                    # Só marca como consultado se a busca rodou sem erros críticos
                    self.repository.marcar_como_consultado(tel_raw)

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

                # Checkpoint periódico a cada N registros
                if idx % self.config.intervalo_checkpoint == 0:
                    trecho = novos_resultados[ultimo_salvo:]
                    if trecho:
                        self.repository.salvar_resultados(trecho)
                        ultimo_salvo = len(novos_resultados)
                        log.info(f"  ✔ Checkpoint: {ultimo_salvo} registro(s) salvos no CSV.")

                # Intervalo obrigatório de 10 segundos entre consultas no Corban
                if idx < total:
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
