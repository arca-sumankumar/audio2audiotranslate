"""Devanagari -> Indic script transliteration for IndicTrans2 postprocessing.

IndicTrans2 en->indic checkpoints emit Devanagari for every Indic target; the
official toolkit converts to the native script. This module provides the same
character-level akshara mapping without extra dependencies.
"""

DEVA_DIGITS = "०१२३४५६७८९"
DEVA_VOWELS = "अआइईउऊऋॠऌॡएऐओऔऍऑ"
DEVA_CONS = "कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहळऱ"
DEVA_NUKTA = "क़ख़ग़ज़ड़ढ़फ़य़"
DEVA_MATRAS = "ािीुूृॄेैोौॅॉ"
DEVA_OTHER = "ंःँ्"

_VOWELS = {
    "gu": "અઆઇઈઉઊઋૠઌૡએઐઓઔઍઑ",
    "bn": "অআইঈউঊঋৠঌৡএঐওঔএও",
    "as": "অআইঈউঊঋৠঌৡএঐওঔএও",
    "kn": "ಅಆಇಈಉಊಋೠಌೡಎಏಓಔಎಒ",
    "ml": "അആഇഈഉഊഋൠഌൡഎഏഓഔഎഒ",
    "te": "అఆఇఈఉఊఋౠఌౡఎఏఓఔఎఒ",
    "or": "ଅଆଇଈଉଊଋୠଌୡଏଐଓଔଏଓ",
    "ta": {
        "अ": "அ", "आ": "ஆ", "इ": "இ", "ई": "ஈ", "उ": "உ", "ऊ": "ஊ",
        "ऋ": "ரு", "ॠ": "ரு",
        "ए": "எ", "ऐ": "ஐ", "ओ": "ஒ", "औ": "ஔ", "ऍ": "எ", "ऑ": "ஒ",
    },
    "pa": {
        "अ": "ਅ", "आ": "ਆ", "इ": "ਇ", "ई": "ਈ", "उ": "ਉ", "ऊ": "ਊ",
        "ए": "ਏ", "ऐ": "ਐ", "ओ": "ਓ", "औ": "ਔ",
    },
}

_CONS = {
    "gu": "કખગઘઙચછજઝઞટઠડઢણતથદધનપફબભમયરલવશષસહળ઱",
    "bn": "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলবশষসহলর",
    "as": "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযৰলৱশষসহলৰ",
    "ta": "ககககஙசசஜஜஞடடடடணததததநபபபபமயரலவஶஷஸஹளற",
    "kn": "ಕಖಗಘಙಚಛಜಝಞಟಠಡಢಣತಥದಧನಪಫಬಭಮಯರಲವಶಷಸಹಳಱ",
    "ml": "കഖഗഘങചഛജഝഞടഠഡഢണതഥദധനപഫബഭമയരലവശഷസഹളറ",
    "te": "కఖగఘఙచఛజఝఞటఠడఢణతథదధనపఫబభమయరలవశషసహళఱ",
    "or": "କଖଗଘଙଚଛଜଝଞଟଠଡଢଣତଥଦଧନପଫବଭମୟରଲୱଶଷସହଳର",
    "pa": "ਕਖਗਘਙਚਛਜਝਞਟਠਡਢਣਤਥਦਧਨਪਫਬਭਮਯਰਲਵਸ਼ਸਸਹਲਰ",
}

_NUKTA = {
    "gu": "કખગજડઢફય",
    "bn": "কখগজডঢফয",
    "as": "কখগজডঢফয",
    "ta": "கககஜடடபய",
    "kn": "ಕಖಗಜಡಢಫಯ",
    "ml": "കഖഗജഡഢഫയ",
    "te": "కఖగజడఢఫయ",
    "or": "କଖଗଜଡଢଫଯ",
    "pa": "ਕਖਗਜਡਢਫਯ",
}

_MATRAS = {
    "gu": "ાિીુૂૃૄેૈોૌૅૉ",
    "bn": "ািীুূৃৄেৈোৌেো",
    "as": "ািীুূৃৄেৈোৌেো",
    "kn": "ಾಿೀುೂೃೄೆೇೋೌ೅ೊ",
    "ml": "ാിീുൂൃൄെേോൌെൊ",
    "te": "ాిీుూృౄెేోౌెొ",
    "or": "ାିୀୁୂୃୃେୈୋୌେୋ",
    "ta": {
        "ा": "ா", "ि": "ி", "ी": "ீ", "ु": "ு", "ू": "ூ",
        "े": "ெ", "ै": "ே", "ो": "ொ", "ौ": "ோ",
    },
    "pa": {
        "ा": "ਾ", "ि": "ਿ", "ी": "ੀ", "ु": "ੁ", "ू": "ੂ",
        "े": "ੇ", "ै": "ੈ", "ो": "ੋ", "ौ": "ੌ",
    },
}

_OTHER = {
    "gu": {"ं": "ં", "ः": "ઃ", "ँ": "ઁ", "्": "્", "ऽ": "ઽ", "ॐ": "ૐ"},
    "bn": {"ं": "ং", "ः": "ঃ", "ँ": "ঁ", "्": "্", "ॐ": "ওঁ"},
    "as": {"ं": "ং", "ः": "ঃ", "ँ": "ঁ", "्": "্", "ॐ": "ওঁ"},
    "ta": {"ं": "ஂ", "ः": "ஃ", "्": "்", "ॐ": "ஓம்", "ऩ": "ன"},
    "kn": {"ं": "ಂ", "ः": "ಃ", "्": "್", "ऽ": "ಽ", "ॐ": "೐"},
    "ml": {"ं": "ം", "ः": "ഃ", "्": "്", "ऽ": "ഽ", "ॐ": "ഓം", "ऴ": "ഴ"},
    "te": {"ं": "ం", "ः": "ః", "्": "్", "ॐ": "౐"},
    "or": {"ं": "ଂ", "ः": "ଃ", "ँ": "ଁ", "्": "୍", "ऽ": "ଽ", "ॐ": "ଓଁ"},
    "pa": {"ं": "ਂ", "्": "੍", "़": "਼"},
}

_DIGITS = {
    "gu": "૦૧૨૩૪૫૬૭૮૯",
    "bn": "০১২৩৪৫৬৭৮৯",
    "as": "০১২৩৪৫৬৭৮৯",
    "ta": "௦௧௨௩௪௫௬௭௮௯",
    "kn": "೦೧೨೩೪೫೬೭೮೯",
    "ml": "൦൧൨൩൪൫൬൭൮൯",
    "te": "౦౧౨౩౪౫౬౭౮౯",
    "or": "୦୧୨୩୪୫୬୭୮୯",
    "pa": "੦੧੨੩੪੫੬੭੮੯",
}


def _build(script):
    table = {}
    vowels = _VOWELS.get(script)
    if isinstance(vowels, dict):
        table.update(vowels)
    elif vowels is not None:
        table.update(dict(zip(DEVA_VOWELS, vowels)))
    cons = _CONS.get(script)
    if cons is not None:
        table.update(dict(zip(DEVA_CONS, cons)))
    nukta = _NUKTA.get(script)
    if nukta is not None:
        table.update(dict(zip(DEVA_NUKTA, nukta)))
    matras = _MATRAS.get(script)
    if isinstance(matras, dict):
        table.update(matras)
    elif matras is not None:
        table.update(dict(zip(DEVA_MATRAS, matras)))
    digits = _DIGITS.get(script)
    if digits is not None:
        table.update(dict(zip(DEVA_DIGITS, digits)))
    table.update(_OTHER.get(script, {}))
    for src, base in (("ॆ", "े"), ("ॊ", "ो"), ("ऎ", "ए"), ("ऒ", "ओ")):
        if base in table:
            table.setdefault(src, table[base])
    table.setdefault("़", "")
    return table


_TABLES = {s: _build(s) for s in set(_VOWELS)}


def _validate():
    for script in _VOWELS:
        if script == "en":
            continue
        if script in ("hi", "mr"):
            continue
        if script not in _TABLES:
            raise ValueError("missing table for %s" % script)
        for src in DEVA_VOWELS:
            if src in _TABLES[script]:
                if _TABLES[script][src] == src and script in ("pa",):
                    continue
        for kind, src in ((_VOWELS, DEVA_VOWELS), (_CONS, DEVA_CONS),
                          (_NUKTA, DEVA_NUKTA), (_MATRAS, DEVA_MATRAS),
                          (_DIGITS, DEVA_DIGITS)):
            data = kind.get(script)
            if isinstance(data, str) and len(data) != len(src):
                raise ValueError(
                    "bad %s length for %s: %d" % (kind, script, len(data)))


_validate()


def devanagari_to_script(text, script):
    table = _TABLES.get(script)
    if not table:
        return text
    return "".join(table.get(ch, ch) for ch in text)
