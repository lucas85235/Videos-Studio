"""Carimbo de veredito negativo (FALSO, GOLPE, FAKE) só sobre o material do golpe.

O carimbo girado fala do que está NA TELA. "GOLPE" sobre um quadro do trailer, "FALSO" sobre a matéria da Dexerto ou
sobre a arte oficial de Jason e Lucia diz ao espectador que o trailer, a imprensa ou a arte é que são falsos (QA do
prj_gq4krrs11w). Veredito negativo só cabe sobre o material do golpista (o site falso, o instalador, o post de acesso
pago, o print do malware), sobre ilustração (IA, banco, foto livre, desenho do app) ou sobre o cartão de texto que
mostra a afirmação. Material oficial e de imprensa (`official`, `capture`) só leva veredito negativo quando a
descrição dele diz que é o artefato do golpe ("Site copia o Extended Look e adiciona botão falso").

Usado na revisão do storyboard (`storyboard_check.fix_verdict_stamps`, que tira o carimbo do documento) e na
resolução da timeline (`timeline.drop_false_verdict`, que tira só da tela, para o projeto já pronto e a edição à mão).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from studio.core.text import normalize_token

if TYPE_CHECKING:
    from studio.core.project.document import MediaRef
    from studio.core.project.pool import PoolItem

# Veredito que acusa o que está na tela de ser falso ou golpe (sem acento, minúsculo: `normalize_token`).
NEGATIVE_VERDICTS = frozenset({
    "falso", "falsa", "falsos", "falsas", "fake", "fakes", "golpe", "golpes", "golpista", "scam", "fraude", "fraud",
    "mentira", "hoax", "false", "falsificado", "falsificada", "estafa", "bulo", "virus", "malware", "pirata",
})  # fmt: skip

# Material que é do mundo real e de quem não aplica golpe: o carimbo negativo sobre ele acusa a fonte.
TRUE_MATERIAL_TIERS = frozenset({"official", "capture"})

# Descrição de material que É o artefato do golpe: o site/página/botão/download/instalador/anúncio falso, o print do
# malware, a cópia do site oficial. Só o título de uma matéria SOBRE golpe ("GTA 6 vira isca para roubar dados") não
# basta: a foto de abertura da matéria é arte ou trailer.
_EVIDENCE_RE = re.compile(
    r"\b(site|pagina|página|botao|botão|download|instalador|installer|anuncio|anúncio|post|perfil|app|aplicativo|"
    r"loja|checkout|link|e-?mail|mensagem|janela|tela|arquivo|iso|exe|demo|launcher|page|website|button|ad|store)"
    r"\b[^.;|—]{0,40}\b(fals[oa]s?|fake|golpe|golpista|fraudulent[oa]s?|clonad[oa]s?|malicios[oa]s?|"
    r"infectad[oa]s?|phishing|scam|fraud)\b"
    r"|\b(fals[oa]s?|fake|clonad[oa]s?|malicios[oa]s?|fraudulent[oa]s?)\b[^.;|—]{0,20}\b(site|pagina|página|"
    r"botao|botão|download|instalador|installer|anuncio|anúncio|post|perfil|app|loja|link|iso|demo|launcher|page|"
    r"website|store)\b"
    r"|\b(print|captura|screenshot)\b[^.;|—]{0,30}\b(golpe|malware|phishing|scam|virus|vírus|golpista)\b"
    r"|\bsite\s+cop(ia|ia-se|iando)\b|\bphishing\b|\bgolpista\b",
    re.IGNORECASE,
)


def is_negative(stamp: str | None) -> bool:
    """O carimbo acusa o que está na tela de ser falso ou golpe ("FALSO", "É GOLPE", "FAKE")."""
    words = re.split(r"[\s·•|/—–:,.!?()-]+", stamp or "")
    return any(normalize_token(w) in NEGATIVE_VERDICTS for w in words if w)


def _describes_scam(material: MediaRef | PoolItem | None, extra: str = "") -> bool:
    if material is None:
        return False
    texts = [extra, getattr(material, "desc", ""), getattr(material, "title", "")]
    return any(_EVIDENCE_RE.search(t or "") for t in texts)


def allows_negative(prov: str, material: MediaRef | PoolItem | None, extra: str = "") -> bool:
    """O veredito negativo pode ficar sobre este material? Fora do oficial e da imprensa, sim (ilustração, banco, foto
    livre, desenho do app, arquivo do usuário). Oficial e captura só quando a descrição diz que é o artefato do golpe.
    `extra`: descrição do item do acervo de onde a mídia veio (a `MediaRef` não guarda a descrição)."""
    if prov not in TRUE_MATERIAL_TIERS:
        return True
    if prov == "capture" and (material is None or getattr(material, "editorial", True)):
        # Captura de imprensa: o título da matéria fala do golpe ("Sites falsos ... roubam"), mas a tela é a matéria.
        return False
    return _describes_scam(material, extra)
