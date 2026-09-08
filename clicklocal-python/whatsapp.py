from urllib.parse import quote


def limpiar_numero_whatsapp(numero_raw):
    """
    Normaliza números para WhatsApp Argentina.

    Casos esperados:
    - 3434150049      -> 5493434150049
    - 03434150049     -> 5493434150049
    - 543434150049    -> 5493434150049
    - 5493434150049   -> 5493434150049

    Nota: para celulares argentinos WhatsApp requiere 54 + 9 + característica + número.
    """
    numero = "".join(ch for ch in str(numero_raw or "") if ch.isdigit())

    if numero.startswith("00"):
        numero = numero[2:]

    while numero.startswith("0"):
        numero = numero[1:]

    if not numero:
        return ""

    if numero.startswith("549"):
        return numero

    if numero.startswith("54"):
        resto = numero[2:]
        if resto.startswith("9"):
            return numero
        return f"549{resto}"

    return f"549{numero}"


def construir_url_whatsapp(numero_raw, mensaje):
    numero = limpiar_numero_whatsapp(numero_raw)

    if not numero:
        return ""

    return f"https://wa.me/{numero}?text={quote(mensaje)}"
