"""Carimbo de veredito negativo (FALSO, GOLPE, FAKE) só sobre o material do golpe.

O carimbo girado fala do que está NA TELA. "GOLPE" sobre um quadro do trailer, "FALSO" sobre a matéria da Dexerto ou
sobre a arte oficial de Jason e Lucia diz ao espectador que o trailer, a imprensa ou a arte é que são falsos (QA do
prj_gq4krrs11w). Veredito negativo só cabe sobre o material do golpista (o site falso, o instalador, o post de acesso
pago, o print do malware), sobre ilustração (IA, banco, foto livre, desenho do app) ou sobre o cartão de texto que
mostra a afirmação em fundo liso ou ilustração (o cartão com o trailer desfocado de fundo e "Fonte: Rockstar Games"
também acusa a Rockstar). Material oficial e de imprensa (`official`, `capture`) só leva veredito negativo quando a
descrição dele diz que é o artefato do golpe ("Site copia o Extended Look e adiciona botão falso").

Usado na revisão do storyboard (`storyboard_check.fix_verdict_stamps`, que tira o carimbo do documento) e na
resolução da timeline (`timeline.drop_false_verdict`, que tira só da tela, para o projeto já pronto e a edição à mão).

Veredito positivo (CONFIRMADO, VERDADE) ao lado de uma frase negativa na tela ("PC: NÃO ANUNCIADO", "NÃO EXISTE")
lê como contradição: confirmado o quê? O carimbo só casa com o texto quando confirma uma afirmação ou diz quem
confirma ("CONFIRMADO PELA ROCKSTAR"); solto ao lado do verbo negado ele sai (`ambiguous_positive`; "SEM ATRASO" não é
negação), na revisão do storyboard (`storyboard_check.fix_ambiguous_stamps`) e na timeline
(`timeline.drop_ambiguous_verdict`).
"""

from __future__ import annotations

import re
import unicodedata
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

# Descrição de material que É o artefato do golpe: o site/página/botão/download/instalador/anúncio falso (singular ou
# plural), o print do malware, a cópia do site oficial. Só o título de uma matéria SOBRE golpe ("GTA 6 vira isca para
# roubar dados") não basta: a foto de abertura da matéria é arte ou trailer. Casado sobre o texto sem acento e minúsculo
# (`_plain`).
_THING = (
    r"(sites?|paginas?|bot(?:ao|oes)|downloads?|instaladore?s?|installers?|anuncios?|posts?|perf(?:il|is)|apps?|"
    r"aplicativos?|lojas?|checkouts?|links?|e-?mails?|mensage(?:m|ns)|janelas?|telas?|arquivos?|isos?|exes?|demos?|"
    r"launchers?|pages?|websites?|buttons?|ads?|stores?|pop-?ups?|alertas?|avisos?)"
)
_FAKE = r"(fals[oa]s?|fakes?|golpes?|golpistas?|fraudulent[oa]s?|clonad[oa]s?|malicios[oa]s?|infectad[oa]s?|phishing|scams?|fraud)"
_FAKE_BEFORE = r"(fals[oa]s?|fakes?|clonad[oa]s?|malicios[oa]s?|fraudulent[oa]s?)"
_EVIDENCE_RE = re.compile(
    rf"\b{_THING}\b[^.;|—]{{0,40}}\b{_FAKE}\b"
    rf"|\b{_FAKE_BEFORE}\b[^.;|—]{{0,20}}\b{_THING}\b"
    r"|\b(print|captura|screenshot)s?\b[^.;|—]{0,30}\b(golpes?|malware|phishing|scams?|virus|golpistas?)\b"
    r"|\bsite\s+cop(ia|iado|iando)\b|\bphishing\b|\bgolpistas?\b"
)
# Aviso sobre o golpe (o post oficial "Rockstar alerta: cuidado com download falso", a dica de como se proteger): o
# material é de quem avisa, não do golpista; o carimbo negativo sobre ele acusaria quem avisa.
_WARNING_RE = re.compile(
    r"\b(alert(a|am|ou|ando)?|avis(o|a|am|ou)|cuidado|advert(e|em|encia|iu)|warn(s|ing|ed)?|beware|alerts?|"
    r"comunicado|nota oficial|como (se proteger|identificar|evitar)|dicas?|protej[ae])\b"
)
# "Alerta falso de vírus", "aviso fake": o pop-up do golpe, não um aviso sobre ele.
_FAKE_WARNING_RE = re.compile(r"\b(alert(a|as|s)?|avisos?|warnings?)\s+(fals[oa]s?|fakes?)\b")
# Palavra que nega o veredito que vem depois dela: "NÃO É GOLPE", "NADA DE FAKE", "NOT FAKE" é veredito positivo.
_NEGATORS = frozenset({"nao", "nem", "nada", "sem", "nunca", "not", "no", "never", "jamais"})


def _plain(text: str) -> str:
    """Minúsculo e sem acento (a pontuação fica: ela separa as frases da descrição)."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


# Veredito positivo explícito: quem confirma é a fonte do cartão ("CONFIRMADO" com "Fonte: Rockstar Games"); ao lado
# de frase negativa na tela ele confunde (`ambiguous_positive`). Aviso ("ATENÇÃO", "ALERTA", "CUIDADO", "DICA") e
# rótulo do vídeo ("BOATO", "MITO") não são afirmação de fonte nenhuma. Sem acento, minúsculo.
POSITIVE_VERDICTS = frozenset({
    "confirmado", "confirmada", "confirmados", "confirmadas", "confirma", "confirmed", "confirmo", "verificado",
    "verificada", "verified", "oficial", "official", "verdade", "verdadeiro", "verdadeira", "true", "fato", "fact",
    "real", "anunciado", "anunciada", "announced", "comprovado", "comprovada", "proven", "certo", "cierto", "verdad",
})  # fmt: skip


def _stamp_words(stamp: str | None) -> list[str]:
    return [normalize_token(w) for w in re.split(r"[\s·•|/—–:,.!?()-]+", stamp or "") if w]


# Negação de frase na tela: o verbo ou o estado negado ("PC: NÃO ANUNCIADO", "NÃO EXISTE", "NUNCA SAIU", "NOT ON PC",
# "ISN'T OUT"). Fica de fora a negação de quantidade ou de preposição ("SEM ATRASO", "SEM ADIAMENTO", "CELULAR NENHUM
# RODA", "NEM DEMO"): ela afirma um estado, e "CONFIRMADO" ao lado lê certo. "no" só em espanhol ("NO EXISTE"): em
# português é "em o" ("no site") e em inglês abre nome próprio ("No Man's Sky").
_TEXT_NEGATORS = frozenset({
    "nao", "nunca", "jamais", "inexistente", "not", "never", "isnt", "arent", "wasnt", "werent", "doesnt", "dont",
    "didnt", "wont", "cant", "cannot", "hasnt", "havent",
})  # fmt: skip
_TEXT_NEGATORS_ES = frozenset({"no", "jamas"})
# Carimbo que diz quem confirma ("CONFIRMADO PELA ROCKSTAR", "CONFIRMED BY ROCKSTAR"): é a fala da fonte, não um
# status contra outro, e casa com a frase negada.
_ATTRIBUTION = frozenset({"pela", "pelo", "por", "segundo", "by", "per", "according", "segun"})


def _tokens(text: str | None) -> list[str]:
    return [t for w in re.split(r"[\s·•|/—–:,.!?()\-]+", text or "") if (t := normalize_token(w))]


def is_positive(stamp: str | None) -> bool:
    """O carimbo confirma a afirmação da tela ("CONFIRMADO", "É VERDADE", "OFICIAL: CONFIRMADO")."""
    words = _tokens(stamp)
    positive = any(
        w in POSITIVE_VERDICTS and not any(n in _NEGATORS for n in words[:i]) for i, w in enumerate(words)
    )
    return positive and not is_negative(stamp)


def attributed(stamp: str | None) -> bool:
    """O carimbo diz quem confirma ("CONFIRMADO PELA ROCKSTAR", "CONFIRMED BY ROCKSTAR")."""
    return any(w in _ATTRIBUTION for w in _tokens(stamp))


def negates(text: str | None, lang: str = "pt-BR") -> bool:
    """A frase da tela nega um verbo ou um estado ("PC: NÃO ANUNCIADO", "PC HOJE: NÃO EXISTE", "NOT ON PC"). "SEM
    ATRASO" e "NENHUM" não contam: afirmam o estado."""
    extra = _TEXT_NEGATORS_ES if (lang or "").lower().startswith("es") else frozenset()
    return any(t in _TEXT_NEGATORS or t in extra for t in _tokens(text))


def ambiguous_positive(stamp: str | None, texts: list[str], lang: str = "pt-BR") -> bool:
    """Veredito positivo solto ao lado de frase negada na tela: "CONFIRMADO" com "PC: NÃO ANUNCIADO" lê como
    contradição (confirma a negação? confirma o PC?). O carimbo sai; o texto e a fonte do cartão já dizem quem afirma.
    Com a fonte no carimbo ("CONFIRMADO PELA ROCKSTAR") ele fica: é a fala de alguém sobre a frase."""
    return is_positive(stamp) and not attributed(stamp) and any(negates(t, lang) for t in texts if t)


def is_negative(stamp: str | None) -> bool:
    """O carimbo acusa o que está na tela de ser falso ou golpe ("FALSO", "É GOLPE", "FAKE"). Com negação antes do
    veredito ("NÃO É GOLPE", "NOT FAKE"), não acusa: é o veredito positivo."""
    words = _stamp_words(stamp)
    for i, word in enumerate(words):
        if word in NEGATIVE_VERDICTS and not any(w in _NEGATORS for w in words[:i]):
            return True
    return False


def describes_scam(text: str) -> bool:
    """A descrição diz que o material é o artefato do golpe (o site, o download, o anúncio falso; o print do golpe), e
    não um aviso sobre ele."""
    plain = _plain(text)
    return bool(_EVIDENCE_RE.search(plain)) and not _WARNING_RE.search(_FAKE_WARNING_RE.sub(" ", plain))


def _describes_scam(material: MediaRef | PoolItem | None, extra: str = "") -> bool:
    if material is None:
        return False
    texts = [extra, getattr(material, "desc", ""), getattr(material, "title", "")]
    return any(describes_scam(t or "") for t in texts)


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
