from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    """Configurações centralizadas da aplicação."""

    usuario: str = "ti@37277"
    senha: str = "Safira@26"
    site_url: str = "https://gestao.sistemacorban.com.br"

    arquivo_entrada: str = "telefones-base safira-menos juros.csv"
    arquivo_saida: str = "resultados_telefones.csv"
    arquivo_historico: str = "telefones_consultados.txt"

    modo_visivel: bool = True
    tempo_entre_buscas: int = 10  # Intervalo obrigatório de 10 segundos
    timeout_resposta: int = 20
    limite_registros: int = 5000
    intervalo_checkpoint: int = 5

    def resolver_arquivo_entrada(self) -> Path:
        """Resolve o arquivo de entrada com suporte a fallbacks comuns."""
        candidatos = [
            self.arquivo_entrada,
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
