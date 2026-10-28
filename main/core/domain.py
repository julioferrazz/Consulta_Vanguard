import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Credencial:
    """Representa uma credencial de acesso ao portal Sistema Corban."""

    usuario: str
    senha: str


@dataclass
class ResultadoClienteFiltro:
    """Representa um cliente com benefício aprovado pelos filtros de idade e empréstimo."""

    cpf: str
    idade: int
    tel1: str
    tel2: str
    parcelas_pagas: int

    def para_dicionario(self) -> dict[str, str | int]:
        """Converte para o formato das colunas do CSV de saída."""
        return {
            "CPF": self.cpf,
            "Idade": self.idade,
            "Tel 1": self.tel1,
            "Tel 2": self.tel2,
            "Parcelas Pagas": self.parcelas_pagas,
        }


class LimiteConsultasExcedido(Exception):
    """Lançada quando a conta ativa atinge o limite diário de consultas do portal."""

    pass


class ErroParadaImediata(Exception):
    """Lançada em situações de falha crítica para interromper com segurança."""

    pass


def limpar_cpf(cpf: str) -> str:
    """Remove pontuação e caracteres não numéricos, completando com zeros à esquerda."""
    digitos = "".join(filter(str.isdigit, str(cpf)))
    if digitos and len(digitos) <= 11:
        return digitos.zfill(11)
    return digitos


def formatar_cpf(cpf: str) -> str:
    """Retorna o CPF formatado no padrão 000.000.000-00."""
    c = limpar_cpf(cpf)
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return cpf


def formatar_telefone(tel: str) -> str:
    """Formata telefone brasileiro: (XX) XXXXX-XXXX para celular ou (XX) XXXX-XXXX para fixo."""
    digitos = "".join(filter(str.isdigit, str(tel)))
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    elif len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return tel.strip() if tel else "N/A"


def calcular_idade(texto: str) -> Optional[int]:
    """Calcula a idade a partir de string com data no formato DD/MM/AAAA."""
    if not texto:
        return None

    m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", texto)
    if not m:
        return None

    try:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        nasc = datetime(ano, mes, dia)
        hoje = datetime.now()
        idade = hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
        return idade if 0 <= idade <= 120 else None
    except ValueError:
        return None
