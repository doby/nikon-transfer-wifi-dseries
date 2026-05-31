"""Command-line entry point."""

import argparse
import logging
from pathlib import Path

from .log import setup as setup_logging
from .protocol import PTPIP_HOST_DEFAULT, PTPIP_PORT, IMAGE_EXTENSIONS
from .transfer import discover_camera, transfer_photos
from .utils import format_size

log = logging.getLogger("nikon_transfer.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transfert Wi-Fi Nikon D5300 → Mac via PTP/IP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  nikon-transfer
  nikon-transfer --dest ~/Bureau/Photos --new-only
  nikon-transfer --host 192.168.0.10 --ext .nef .jpg
  nikon-transfer --dry-run
  nikon-transfer --debug                        # paquets hex sur stderr + fichier log
  nikon-transfer --log-file ~/Desktop/cam.log  # chemin personnalisé
        """,
    )
    parser.add_argument(
        "--host",
        default=PTPIP_HOST_DEFAULT,
        help=f"IP de la caméra (défaut : {PTPIP_HOST_DEFAULT})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path.home() / "Pictures" / "Nikon_D5300",
        help="Dossier de destination (défaut : ~/Pictures/Nikon_D5300)",
    )
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="Ignorer les fichiers déjà présents dans la destination",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lister les fichiers sans télécharger",
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        default=list(IMAGE_EXTENSIONS),
        help="Extensions à transférer (défaut : jpg nef tif png …)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout socket en secondes (défaut : 10)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Niveau DEBUG sur la console + hex de chaque paquet dans le log",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        metavar="CHEMIN",
        help="Fichier de log (défaut : nikon_transfer_YYYYMMDD_HHMMSS.log dans le dossier courant)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Désactiver l'écriture du fichier de log",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    log_path = setup_logging(
        debug=args.debug,
        log_file=args.log_file,
        auto_log=not args.no_log_file,
    )

    extensions = {
        e.lower() if e.startswith(".") else f".{e.lower()}"
        for e in args.ext
    }

    print()
    print("═" * 55)
    print("  Nikon D5300 — Transfert Wi-Fi")
    print("═" * 55)
    print(f"  Caméra  : {args.host}:{PTPIP_PORT}")
    print(f"  Dest    : {args.dest}")
    print(f"  Exts    : {', '.join(sorted(extensions))}")
    print(f"  Options : new-only={args.new_only}  dry-run={args.dry_run}  debug={args.debug}")
    if log_path:
        print(f"  Log     : {log_path}")
    print()

    log.info(
        "Démarrage — host=%s dest=%s new_only=%s dry_run=%s debug=%s",
        args.host, args.dest, args.new_only, args.dry_run, args.debug,
    )

    print("1. Recherche de la caméra …")
    if not discover_camera(args.host):
        msg = (
            f"\n  ✗ Impossible de joindre {args.host}:{PTPIP_PORT}\n\n"
            "  Vérifiez :\n"
            "    • Le D5300 est allumé et le Wi-Fi activé\n"
            "      (Menu → Setup ⚙ → Wi-Fi → Enable)\n"
            "    • Votre Mac est connecté au réseau créé par la caméra\n"
            '      (SSID ressemble à "Nikon_XXXXXXXX")\n'
            f"    • L'IP de la caméra est bien {args.host}\n"
            "      (vérifiable dans Préférences Réseau du Mac)\n"
        )
        print(msg)
        log.error("Caméra non joignable sur %s:%d", args.host, PTPIP_PORT)
        return 1

    print("  Caméra détectée ✓\n")

    print("2. Connexion PTP/IP …")
    try:
        stats = transfer_photos(
            host=args.host,
            dest=args.dest,
            new_only=args.new_only,
            dry_run=args.dry_run,
            extensions=extensions,
            timeout=args.timeout,
        )
    except ConnectionError as e:
        print(f"\n  ✗ Erreur de connexion : {e}")
        log.error("Erreur de connexion : %s", e)
        return 1
    except KeyboardInterrupt:
        print("\n  Transfert interrompu par l'utilisateur.")
        log.info("Interrompu par l'utilisateur")
        return 0

    print()
    print("═" * 55)
    print("  Résumé")
    print("═" * 55)
    print(f"  ✓ Transférés : {stats['transferred']}")
    print(f"  ↷ Ignorés    : {stats['skipped']}")
    print(f"  ✗ Erreurs    : {stats['errors']}")
    print(f"  Volume total : {format_size(stats['bytes'])}")
    print(f"  Destination  : {args.dest}")
    if log_path:
        print(f"  Log complet  : {log_path}")
    print()

    log.info(
        "Terminé — transférés=%d ignorés=%d erreurs=%d octets=%d",
        stats["transferred"], stats["skipped"], stats["errors"], stats["bytes"],
    )
    return 0
