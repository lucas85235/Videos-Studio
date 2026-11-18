"""Batida séria: a regra única de "nada cômico aqui" (ADR 0004 §3.0).

Duas fontes se juntam: a marca do storyboard (`Scene.mark.serious`, decidida pelo LLM) e os termos de cena séria do
playbook (vítima, menor de idade, morte, crise, racismo, maus-tratos), procurados na fala da cena. O editor-planejador
(sem meme, gag, shake nem SFX cômico) e o apresentador (sem avatar) usam `is_serious`; o termo achado sai em
`serious_term` para o aviso.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from studio.core.project.presenter import fold

if TYPE_CHECKING:
    from studio.core.project.document import Scene

# Cena séria (playbook: vítimas, menores, pessoas em crise, ataque racial, maus-tratos, Kurtaj).
SERIOUS_TERMS = (
    "vitima", "vitimas", "menor de idade", "menores de idade", "crianca", "criancas", "morte", "mortes", "morreu",
    "morreram", "morto", "mortos", "assassin", "homicid", "suicid", "crise", "racismo", "racista", "racial",
    "maus-tratos", "maus tratos", "abuso sexual", "estupro", "kurtaj", "luto", "tragedia", "tragedias", "crises",
)
SERIOUS_PREFIXES = ("assassin", "homicid", "suicid")
"""Os termos sérios que valem como começo de palavra (assassinato, homicídio, suicídio...); os outros são palavra
inteira ("luto" não pega "lutou")."""
SERIOUS_NOT = ("assassin's creed", "assassins creed", "assassin s creed")
"""Nomes que contêm um termo sério e não são cena séria (jogos)."""


def serious_term(text: str) -> str | None:
    """O termo de cena séria que aparece no texto (sem acento; palavra inteira, prefixo só em `SERIOUS_PREFIXES`)."""
    folded = " " + " ".join(fold(text).replace("-", " ").split()) + " "
    for name in SERIOUS_NOT:
        folded = folded.replace(name, " ")
    for term in SERIOUS_TERMS:
        t = " ".join(fold(term).replace("-", " ").split())
        tail = "" if term in SERIOUS_PREFIXES else r"(?![a-z0-9])"
        if re.search(r"(?<![a-z0-9])" + re.escape(t) + tail, folded):
            return term
    return None


def is_serious(scene: Scene, text: str = "") -> bool:
    """A batida é séria: marcada pelo storyboard (`mark.serious`) OU com termo sério na fala (`text`: as palavras
    da cena, mais o texto de tela se houver)."""
    if scene.mark is not None and scene.mark.serious:
        return True
    return bool(text) and serious_term(text) is not None
