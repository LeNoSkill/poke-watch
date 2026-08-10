#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Surveillance Belgium Poke Center - serie "30e Anniversaire".

Detecte deux evenements et envoie une notif push (via ntfy.sh) :
  1) un NOUVEAU produit apparait  -> les visuels sont en ligne
  2) un produit devient COMMANDABLE -> la precommande est ouverte

Aucune dependance externe : uniquement la bibliotheque standard Python.
Configuration via variables d'environnement (voir plus bas / README).
"""

import json
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

# ----------------------------------------------------------------------
# Configuration (modifiable via variables d'environnement)
# ----------------------------------------------------------------------
SHOP = os.environ.get("SHOP", "https://belgiumpokecenter.com").rstrip("/")

# Handle (identifiant d'URL) de la collection a surveiller.
# "30e Anniversaire" -> handle Shopify "30ᵉ-anniversaire"
COLLECTION_HANDLE = os.environ.get("COLLECTION_HANDLE", "30\u1d49-anniversaire")

# Filet de securite : on scanne aussi TOUT le catalogue et on garde les
# produits dont le titre contient l'un de ces mots (au cas ou un produit
# serait publie avant d'etre range dans la collection). Vide = desactive.
KEYWORDS = [k.strip() for k in os.environ.get(
    "WATCH_KEYWORDS", "anniversaire,anniversary").split(",") if k.strip()]

# ntfy : sujet (topic) sur lequel envoyer les notifs. A DEFINIR.
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

STATE_FILE = os.environ.get("STATE_FILE", "state.json")

UA = ("Mozilla/5.0 (compatible; PokeWatcher/1.0; "
      "+https://github.com/) collector personal alert")
TIMEOUT = 25


# ----------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------
def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        print("  ! erreur GET %s -> %s" % (url, e))
        return None


def product_record(p):
    """Extrait ce qui nous interesse d'un produit Shopify."""
    variants = p.get("variants") or []
    available = any(bool(v.get("available")) for v in variants)
    price = None
    for v in variants:
        if v.get("price") is not None:
            price = v.get("price")
            break
    return {
        "title": p.get("title", "Produit"),
        "handle": p.get("handle", ""),
        "available": available,
        "price": price,
        "url": "%s/products/%s" % (SHOP, p.get("handle", "")),
    }


# ----------------------------------------------------------------------
# Recuperation des produits
# ----------------------------------------------------------------------
def get_collection_products():
    url = "%s/collections/%s/products.json?limit=250" % (
        SHOP, urllib.parse.quote(COLLECTION_HANDLE))
    data = http_get_json(url)
    if not data:
        return {}
    out = {}
    for p in data.get("products", []):
        out[str(p.get("id"))] = product_record(p)
    print("  collection: %d produit(s)" % len(out))
    return out


def get_keyword_products():
    if not KEYWORDS:
        return {}
    norm_kw = [strip_accents(k) for k in KEYWORDS]
    out = {}
    page = 1
    while page <= 15:  # borne de securite (~3750 produits max)
        url = "%s/products.json?limit=250&page=%d" % (SHOP, page)
        data = http_get_json(url)
        if not data:
            break
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            title_n = strip_accents(p.get("title", ""))
            if any(k in title_n for k in norm_kw):
                out[str(p.get("id"))] = product_record(p)
        page += 1
        time.sleep(0.4)  # on reste poli avec le serveur
    if out:
        print("  mots-cles: %d produit(s) correspondant(s)" % len(out))
    return out


def get_all_products():
    merged = {}
    merged.update(get_keyword_products())
    merged.update(get_collection_products())  # la collection prime
    return merged


# ----------------------------------------------------------------------
# Notifications (ntfy)
# ----------------------------------------------------------------------
def notify(title, message, priority=3, tags=None, click=None):
    if not NTFY_TOPIC:
        print("  [notif ignoree : NTFY_TOPIC non defini] %s" % title)
        return
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if tags:
        payload["tags"] = tags
    if click:
        payload["click"] = click
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        NTFY_SERVER, data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read()
        print("  -> notif envoyee : %s" % title)
    except Exception as e:  # noqa: BLE001
        print("  ! echec notif : %s" % e)


# ----------------------------------------------------------------------
# Etat persistant
# ----------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# Logique principale
# ----------------------------------------------------------------------
def run():
    print("== Verification %s ==" % time.strftime("%Y-%m-%d %H:%M:%S"))
    current = get_all_products()
    state = load_state()
    first_run = not state.get("initialized")
    prev = state.get("products", {})

    if first_run:
        n = len(current)
        if n:
            lignes = "\n".join("- %s%s" % (
                v["title"], "  (deja commandable)" if v["available"] else "")
                for v in current.values())
            msg = "%d produit(s) deja en ligne :\n%s" % (n, lignes)
        else:
            msg = ("Collection encore vide. Je te previens des que les "
                   "visuels apparaissent, puis quand la precommande ouvre.")
        notify("Surveillance 30e Anniversaire activee",
               msg, priority=3, tags=["white_check_mark"],
               click="%s/collections/%s" % (
                   SHOP, urllib.parse.quote(COLLECTION_HANDLE)))
    else:
        for pid, cur in current.items():
            old = prev.get(pid)
            if old is None:
                # Nouveau produit : les visuels sont en ligne.
                price = (" - %s EUR" % cur["price"]) if cur["price"] else ""
                notify(
                    "Visuel en ligne : %s" % cur["title"],
                    "Nouveau produit 30e Anniversaire%s.%s" % (
                        price,
                        "\nDeja COMMANDABLE !" if cur["available"] else
                        "\nPas encore commandable (precommande a venir)."),
                    priority=(5 if cur["available"] else 4),
                    tags=(["rotating_light"] if cur["available"] else ["new"]),
                    click=cur["url"])
            elif cur["available"] and not old.get("available"):
                # Transition indisponible -> commandable : precommande ouverte.
                price = (" - %s EUR" % cur["price"]) if cur["price"] else ""
                notify(
                    "PRECOMMANDE OUVERTE : %s" % cur["title"],
                    "C'est commandable maintenant%s. Attention : "
                    "precommande ferme et non annulable." % price,
                    priority=5, tags=["rotating_light", "shopping"],
                    click=cur["url"])

    save_state({"initialized": True, "products": current})
    print("== Fin ==")


if __name__ == "__main__":
    if not NTFY_TOPIC and "--allow-no-topic" not in sys.argv:
        print("ERREUR : la variable NTFY_TOPIC n'est pas definie.\n"
              "Definis-la (ton sujet ntfy prive) avant de lancer le script.")
        sys.exit(1)
    run()
