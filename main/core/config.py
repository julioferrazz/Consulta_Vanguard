import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:
    from core.domain import Credencial
except ImportError:
    from .domain import Credencial

from dotenv import find_dotenv, load_dotenv


def carregar_env() -> None:
    """Carrega as variáveis do arquivo .env usando a biblioteca python-dotenv."""
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file)
    else:
        # Fallback local direto
        locais = [Path(".env"), Path("main/.env"), Path(__file__).resolve().parent.parent / ".env"]
        for p in locais:
            if p.exists():
                load_dotenv(p)
                break


@dataclass
class AppConfig:
    """Configurações centralizadas da aplicação com suporte a múltiplas credenciais."""

    contas: List[Credencial] = field(default_factory=list)
    site_url: str = "https://gestao.sistemacorban.com.br"

    arquivo_entrada: str = "telefones-base safira-menos juros.csv"
    arquivo_saida: str = "resultados_telefones.csv"
    arquivo_historico: str = "telefones_consultados.txt"

    modo_visivel: bool = True
    tempo_entre_buscas: int = 10  # Intervalo obrigatório de 10 segundos
    timeout_resposta: int = 20
    limite_registros: int = 5000
    intervalo_checkpoint: int = 5

    @property
    def usuario(self) -> str:
        """Retorna o usuário da primeira conta disponível (para compatibilidade)."""
        return self.contas[0].usuario if self.contas else ""

    @property
    def senha(self) -> str:
        """Retorna a senha da primeira conta disponível (para compatibilidade)."""
        return self.contas[0].senha if self.contas else ""

    @classmethod
    def carregar(cls) -> "AppConfig":
        """Constrói as configurações a partir do arquivo .env e variáveis de ambiente."""
        carregar_env()

        contas: List[Credencial] = []

        # 1. Tenta carregar lista CORBAN_CONTAS (formato: user1:pass1,user2:pass2,...)
        contas_raw = os.getenv("CORBAN_CONTAS", "").strip()
        if contas_raw:
            itens = [item.strip() for item in contas_raw.split(",") if item.strip()]
            for item in itens:
                if ":" in item:
                    u, s = item.split(":", 1)
                    if u.strip() and s.strip():
                        contas.append(Credencial(usuario=u.strip(), senha=s.strip()))

        # 2. Tenta carregar contas numeradas (CORBAN_USUARIO_1, CORBAN_SENHA_1, ...)
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

        # Parâmetros gerais
        site_url = os.getenv("SITE_URL", "https://gestao.sistemacorban.com.br")
        arquivo_entrada = os.getenv("ARQUIVO_ENTRADA", "telefones-base safira-menos juros.csv")
        arquivo_saida = os.getenv("ARQUIVO_SAIDA", "resultados_telefones.csv")
        arquivo_historico = os.getenv("ARQUIVO_HISTORICO", "telefones_consultados.txt")

        modo_visivel_str = os.getenv("MODO_VISIVEL", "true").lower()
        modo_visivel = modo_visivel_str not in ("false", "0", "nao", "não")

        tempo_entre_buscas = int(os.getenv("TEMPO_ENTRE_BUSCAS", "10"))
        timeout_resposta = int(os.getenv("TIMEOUT_RESPOSTA", "20"))
        limite_registros = int(os.getenv("LIMITE_REGISTROS", "5000"))
        intervalo_checkpoint = int(os.getenv("INTERVALO_CHECKPOINT", "5"))

        return cls(
            contas=contas,
            site_url=site_url,
            arquivo_entrada=arquivo_entrada,
            arquivo_saida=arquivo_saida,
            arquivo_historico=arquivo_historico,
            modo_visivel=modo_visivel,
            tempo_entre_buscas=tempo_entre_buscas,
            timeout_resposta=timeout_resposta,
            limite_registros=limite_registros,
            intervalo_checkpoint=intervalo_checkpoint,
        )

    def resolver_arquivo_entrada(self) -> Path:
        """Resolve o arquivo de entrada com suporte a fallbacks comuns."""
        candidatos = [
            self.arquivo_entrada,
            "telefones-base safira-menos juros.csv",
            "telefones.csv",
            "telefones.xlsx",
            "telefones.txt",
        ]
        for c in candidatos:
            p = Path(c)
            if p.exists():
                return p

        # Fallback: qualquer .csv que contenha 'telefone' no nome
        for p in Path(".").glob("*.csv"):
            if "telefone" in p.name.lower():
                return p

        return Path(self.arquivo_entrada)
