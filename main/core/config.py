import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from core.domain import Credencial
from dotenv import find_dotenv, load_dotenv


def carregar_env() -> None:
    """Carrega as variáveis do arquivo .env com fallbacks nos locais esperados."""
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file)
    else:
        locais = [
            Path(".env"),
            Path("main/.env"),
            Path(__file__).resolve().parent.parent / ".env",
            Path(__file__).resolve().parent.parent.parent / ".env",
        ]
        for p in locais:
            if p.exists():
                load_dotenv(p)
                break


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

    @property
    def usuario(self) -> str:
        """Retorna o usuário da primeira conta disponível."""
        return self.contas[0].usuario if self.contas else ""

    @property
    def senha(self) -> str:
        """Retorna a senha da primeira conta disponível."""
        return self.contas[0].senha if self.contas else ""

    @classmethod
    def carregar(cls) -> "AppConfig":
        """Constrói as configurações a partir do arquivo .env e variáveis de ambiente."""
        carregar_env()

        contas: List[Credencial] = []

        # 1. Carrega lista CORBAN_CONTAS (formato: user1:pass1,user2:pass2,...)
        contas_raw = os.getenv("CORBAN_CONTAS", "").strip()
        if contas_raw:
            itens = [item.strip() for item in contas_raw.split(",") if item.strip()]
            for item in itens:
                if ":" in item:
                    u, s = item.split(":", 1)
                    if u.strip() and s.strip():
                        contas.append(Credencial(usuario=u.strip(), senha=s.strip()))

        # 2. Carrega contas numeradas (CORBAN_USUARIO_1, CORBAN_SENHA_1, ...)
        idx = 1
        while True:
            u = os.getenv(f"CORBAN_USUARIO_{idx}", "").strip()
            s = os.getenv(f"CORBAN_SENHA_{idx}", "").strip()
            if not u:
                break
            if s and Credencial(u, s) not in contas:
                contas.append(Credencial(usuario=u, senha=s))
            idx += 1

        # 3. Fallback para variáveis unitárias CORBAN_USUARIO e CORBAN_SENHA
        if not contas:
            u = os.getenv("CORBAN_USUARIO", "").strip()
            s = os.getenv("CORBAN_SENHA", "").strip()
            if u and s:
                contas.append(Credencial(usuario=u, senha=s))

        # Fallback padrão caso nenhum .env seja encontrado
        if not contas:
            contas = [
                Credencial(usuario="ti@37277", senha="Safira@26"),
                Credencial(usuario="Gestor@37277", senha="Vanguard@@"),
                Credencial(usuario="Supervisao@37277", senha="Safira@26"),
            ]

        site_url = os.getenv("SITE_URL", "https://gestao.sistemacorban.com.br")
        arquivo_entrada = os.getenv("ARQUIVO_ENTRADA", "cpfs.csv")
        arquivo_saida = os.getenv("ARQUIVO_SAIDA", "resultados.csv")
        arquivo_historico = os.getenv("ARQUIVO_HISTORICO", "cpfs_consultados.txt")

        idade_limite = int(os.getenv("IDADE_LIMITE", "80"))
        parcelas_pagas_limite = int(os.getenv("PARCELAS_PAGAS_LIMITE", "10"))
        pular_sem_emp_str = os.getenv("PULAR_SE_SEM_EMPRESTIMO", "true").lower()
        pular_se_sem_emprestimo = pular_sem_emp_str not in ("false", "0", "nao", "não")

        modo_visivel_str = os.getenv("MODO_VISIVEL", "true").lower()
        modo_visivel = modo_visivel_str not in ("false", "0", "nao", "não")

        tempo_espera = int(os.getenv("TEMPO_ESPERA", "2"))
        timeout_resposta = int(os.getenv("TIMEOUT_RESPOSTA", "20"))
        limite_cpfs = int(os.getenv("LIMITE_CPFS", "500"))
        intervalo_checkpoint = int(os.getenv("INTERVALO_CHECKPOINT", "5"))

        return cls(
            contas=contas,
            site_url=site_url,
            arquivo_entrada=arquivo_entrada,
            arquivo_saida=arquivo_saida,
            arquivo_historico=arquivo_historico,
            idade_limite=idade_limite,
            parcelas_pagas_limite=parcelas_pagas_limite,
            pular_se_sem_emprestimo=pular_se_sem_emprestimo,
            modo_visivel=modo_visivel,
            tempo_espera=tempo_espera,
            timeout_resposta=timeout_resposta,
            limite_cpfs=limite_cpfs,
            intervalo_checkpoint=intervalo_checkpoint,
        )

    def resolver_arquivo_entrada(self) -> Path:
        """Resolve o arquivo de entrada com suporte a fallbacks e formatos comuns."""
        candidatos = [
            self.arquivo_entrada,
            "cpfs.csv",
            "cpfs.xlsx",
            "cpfs.xls",
            "cpfs.txt",
        ]
        for c in candidatos:
            p = Path(c)
            if p.exists():
                return p

        for p in Path(".").glob("*.csv"):
            if "cpf" in p.name.lower():
                return p

        return Path(self.arquivo_entrada)
