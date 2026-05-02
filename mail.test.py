import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup

def dekoduj(text):
    parts = decode_header(text)
    result = ""
    for part, encoding in parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8")
        else:
            result += part
    return result

def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode("utf-8", errors="ignore")
    else:
        return msg.get_payload(decode=True).decode("utf-8", errors="ignore")

mail = imaplib.IMAP4_SSL("imap.seznam.cz")
mail.login("realitybot@seznam.cz", "Necum123")

mail.select("inbox")
_, messages = mail.search(None, "ALL")

for msg_id in messages[0].split():
    _, msg_data = mail.fetch(msg_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    odesilatel = dekoduj(msg["from"])
    predmet = dekoduj(msg["subject"])

    if "sreality" in odesilatel.lower() or "Fwd:" in predmet:
        html = get_body(msg)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        print("Předmět:", predmet)
        print(text[:1000])
        print("---")

mail.logout()