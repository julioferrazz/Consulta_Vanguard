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
    """Remove pontuação e completa com zeros à esquerda até 11 dígitos."""
    d = "".join(filter(str.isdigit, str(cpf or "")))
    return d.zfill(11) if 0 < len(d) <= 11 else d


def formatar_cpf(cpf: str) -> str:
    """Retorna o CPF formatado no padrão 000.000.000-00."""
    c = limpar_cpf(cpf)
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}" if len(c) == 11 else str(cpf)


def formatar_telefone(tel: str) -> str:
    """Formata telefone brasileiro: (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    d = "".join(filter(str.isdigit, str(tel or "")))
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return tel.strip() if tel else "N/A"


def calcular_idade(texto: str) -> Optional[int]:
    """Calcula a idade a partir de data no formato DD/MM/AAAA."""
    if not texto or not (m := re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", texto)):
        return None
    try:
        dia, mes, ano = map(int, m.groups())
        hoje = datetime.now()
        idade = hoje.year - ano - ((hoje.month, hoje.day) < (mes, dia))
        return idade if 0 <= idade <= 120 else None
    except ValueError:
        return None
