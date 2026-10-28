import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from core.domain import Credencial
from dotenv import find_dotenv, load_dotenv


def carregar_env() -> None:
    """Carrega as variáveis do arquivo .env com fallbacks nos locais esperados."""
    if p := find_dotenv(usecwd=True):
        load_dotenv(p)
    else:
        for local in (".env", "main/.env"):
            if Path(local).exists():
                load_dotenv(local)
                break


def _env_bool(chave: str, padrao: bool = True) -> bool:
    """Lê booleano de variável de ambiente com valor padrão."""
    return os.getenv(chave, str(padrao)).strip().lower() not in ("false", "0", "nao", "não")


@dataclass
class AppConfig:
    """Configurações centralizadas da aplicação v2 com suporte a rotação e filtros."""

    contas: List[Credencial] = field(default_factory=list)
    site_url: str = "https://gestao.sistemacorban.com.br"

    arquivo_entrada: str = "cpfs.csv"
    arquivo_saida: str = "resultados.csv"
    arquivo_historico: str = "cpfs_consultados.txt"

    idade_limite: int = 80
    parcelas_pagas_limite: int = 10
    pular_se_sem_emprestimo: bool = True

    modo_visivel: bool = True
    tempo_espera: int = 2
    timeout_resposta: int = 20
    limite_cpfs: int = 500
    intervalo_checkpoint: int = 5

    @classmethod
    def carregar(cls) -> "AppConfig":
        """Constrói as configurações a partir do .env e variáveis de ambiente."""
        carregar_env()
        contas: List[Credencial] = []

        # 1. Lista CORBAN_CONTAS (formato: user1:pass1,user2:pass2)
        if raw := os.getenv("CORBAN_CONTAS", "").strip():
            for item in raw.split(","):
                if ":" in item:
                    u, s = item.split(":", 1)
                    if u.strip() and s.strip():
                        contas.append(Credencial(u.strip(), s.strip()))

        # 2. Contas numeradas (CORBAN_USUARIO_1, CORBAN_SENHA_1, ...)
        idx = 1
        while u := os.getenv(f"CORBAN_USUARIO_{idx}", "").strip():
            s = os.getenv(f"CORBAN_SENHA_{idx}", "").strip()
            if s and Credencial(u, s) not in contas:
                contas.append(Credencial(u, s))
            idx += 1

        # 3. Variáveis unitárias CORBAN_USUARIO e CORBAN_SENHA
        if not contas and (u := os.getenv("CORBAN_USUARIO", "").strip()):
            if s := os.getenv("CORBAN_SENHA", "").strip():
                contas.append(Credencial(u, s))

        # Fallback padrão caso nenhum .env seja configurado
        if not contas:
            contas = [
                Credencial("ti@37277", "Safira@26"),
                Credencial("Gestor@37277", "Vanguard@@"),
                Credencial("Supervisao@37277", "Safira@26"),
            ]

        return cls(
            contas=contas,
            site_url=os.getenv("SITE_URL", "https://gestao.sistemacorban.com.br"),
            arquivo_entrada=os.getenv("ARQUIVO_ENTRADA", "cpfs.csv"),
            arquivo_saida=os.getenv("ARQUIVO_SAIDA", "resultados.csv"),
            arquivo_historico=os.getenv("ARQUIVO_HISTORICO", "cpfs_consultados.txt"),
            idade_limite=int(os.getenv("IDADE_LIMITE", "80")),
            parcelas_pagas_limite=int(os.getenv("PARCELAS_PAGAS_LIMITE", "10")),
            pular_se_sem_emprestimo=_env_bool("PULAR_SE_SEM_EMPRESTIMO", True),
            modo_visivel=_env_bool("MODO_VISIVEL", True),
            tempo_espera=int(os.getenv("TEMPO_ESPERA", "2")),
            timeout_resposta=int(os.getenv("TIMEOUT_RESPOSTA", "20")),
            limite_cpfs=int(os.getenv("LIMITE_CPFS", "500")),
            intervalo_checkpoint=int(os.getenv("INTERVALO_CHECKPOINT", "5")),
        )

    def resolver_arquivo_entrada(self) -> Path:
        """Resolve o arquivo de entrada com suporte a fallbacks e formatos comuns."""
        for nome in (self.arquivo_entrada, "cpfs.csv", "cpfs.xlsx", "cpfs.xls", "cpfs.txt"):
            if (p := Path(nome)).exists():
                return p
        for p in Path(".").glob("*.csv"):
            if "cpf" in p.name.lower():
                return p
        return Path(self.arquivo_entrada)
