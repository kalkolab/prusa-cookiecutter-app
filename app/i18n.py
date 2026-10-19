"""
Server-side translations for user-facing error messages.

The UI stores the chosen language in the `lang` cookie; the same cookie (or, before a
choice is made, the Accept-Language header) picks the language of API error messages.
Code raises UserError(key, **params) and the exception handler in main.py renders it.
"""

from typing import Dict, Optional

from fastapi import Request

SUPPORTED_LANGUAGES = ("en", "fr", "ru")
DEFAULT_LANGUAGE = "en"
LANG_COOKIE = "lang"


class UserError(Exception):
    """An error whose message is shown to the user, rendered in their language."""

    def __init__(self, key: str, status_code: int = 400, headers: Optional[Dict[str, str]] = None, **params):
        super().__init__(translate(key, DEFAULT_LANGUAGE, **params))
        self.key = key
        self.status_code = status_code
        self.headers = headers
        self.params = params


MESSAGES: Dict[str, Dict[str, str]] = {
    # Authentication
    "auth_required": {
        "en": "Authentication required. Please sign in with Google.",
        "fr": "Authentification requise. Veuillez vous connecter avec Google.",
        "ru": "Требуется вход. Войдите через Google.",
    },
    "email_unverified": {
        "en": "Token does not contain a verified email address.",
        "fr": "Le jeton ne contient pas d'adresse e-mail vérifiée.",
        "ru": "Токен не содержит подтверждённого адреса электронной почты.",
    },
    "access_denied": {
        "en": "Access denied: Account '{email}' is not on the authorized user list.",
        "fr": "Accès refusé : le compte « {email} » ne figure pas sur la liste des utilisateurs autorisés.",
        "ru": "Доступ запрещён: аккаунта «{email}» нет в списке разрешённых пользователей.",
    },
    "invalid_token": {
        "en": "Invalid Google ID Token.",
        "fr": "Jeton d'identification Google invalide.",
        "ru": "Недействительный токен Google ID.",
    },
    "auth_unavailable": {
        "en": "Authentication service unavailable.",
        "fr": "Service d'authentification indisponible.",
        "ru": "Сервис аутентификации недоступен.",
    },
    # Image & geometry
    "not_image": {
        "en": "Uploaded file is not an image.",
        "fr": "Le fichier envoyé n'est pas une image.",
        "ru": "Загруженный файл не является изображением.",
    },
    "image_too_large": {
        "en": "Image size exceeds 15MB limit.",
        "fr": "L'image dépasse la limite de 15 Mo.",
        "ru": "Размер изображения превышает 15 МБ.",
    },
    "image_decode_failed": {
        "en": "Could not decode image file. Please provide a valid PNG, JPG, or WebP.",
        "fr": "Impossible de lire l'image. Fournissez un fichier PNG, JPG ou WebP valide.",
        "ru": "Не удалось прочитать изображение. Загрузите корректный файл PNG, JPG или WebP.",
    },
    "no_shapes": {
        "en": "No valid shapes detected in image. Try toggling 'Invert Colors' or using an image with higher contrast.",
        "fr": "Aucune forme valide détectée dans l'image. Essayez « Inverser les couleurs » ou une image plus contrastée.",
        "ru": "На изображении не найдено подходящих контуров. Попробуйте «Инвертировать цвета» или более контрастное изображение.",
    },
    "zero_bbox": {
        "en": "Invalid contour bounding box (zero size).",
        "fr": "Contour invalide (taille nulle).",
        "ru": "Некорректный контур (нулевой размер).",
    },
    "mesh_failed": {
        "en": "Failed to construct 3D mesh. The image contours could not be extruded. Try a different image or adjust settings.",
        "fr": "Impossible de construire le modèle 3D à partir des contours. Essayez une autre image ou modifiez les réglages.",
        "ru": "Не удалось построить 3D-модель по контурам изображения. Попробуйте другое изображение или измените настройки.",
    },
    "param_range": {
        "en": "{name} must be between {lo} and {hi} (got {value}).",
        "fr": "{name} doit être compris entre {lo} et {hi} (valeur reçue : {value}).",
        "ru": "{name} должно быть от {lo} до {hi} (получено {value}).",
    },
    "footprint_too_big": {
        "en": "Model footprint {width} × {length} mm does not fit the {printer} bed (max {max_x} × {max_y} mm). Reduce the target size or handle rim width.",
        "fr": "L'empreinte du modèle ({width} × {length} mm) ne tient pas sur le plateau de la {printer} (max. {max_x} × {max_y} mm). Réduisez la taille cible ou la largeur du rebord.",
        "ru": "Модель размером {width} × {length} мм не помещается на стол {printer} (макс. {max_x} × {max_y} мм). Уменьшите целевой размер или ширину бортика.",
    },
    "internal_error": {
        "en": "Internal processing error: {error}",
        "fr": "Erreur interne de traitement : {error}",
        "ru": "Внутренняя ошибка обработки: {error}",
    },
    # Files & library
    "invalid_path": {
        "en": "Invalid path access.",
        "fr": "Chemin d'accès invalide.",
        "ru": "Недопустимый путь.",
    },
    "file_expired": {
        "en": "Requested file not found or expired.",
        "fr": "Fichier introuvable ou expiré.",
        "ru": "Файл не найден или срок его хранения истёк.",
    },
    "file_not_found": {
        "en": "File not found.",
        "fr": "Fichier introuvable.",
        "ru": "Файл не найден.",
    },
    "library_item_not_found": {
        "en": "Library item not found.",
        "fr": "Élément de la bibliothèque introuvable.",
        "ru": "Элемент библиотеки не найден.",
    },
    "invalid_job_id": {
        "en": "Invalid job id.",
        "fr": "Identifiant de tâche invalide.",
        "ru": "Неверный идентификатор задания.",
    },
    "name_empty": {
        "en": "Name must not be empty.",
        "fr": "Le nom ne doit pas être vide.",
        "ru": "Название не может быть пустым.",
    },
    "name_too_long": {
        "en": "Name must be at most {max} characters.",
        "fr": "Le nom doit comporter au plus {max} caractères.",
        "ru": "Название должно быть не длиннее {max} символов.",
    },
    "job_expired": {
        "en": "Generation job not found or expired. Generate the cutter again.",
        "fr": "Génération introuvable ou expirée. Générez l'emporte-pièce à nouveau.",
        "ru": "Результат генерации не найден или устарел. Сгенерируйте формочку заново.",
    },
    "job_stl_missing": {
        "en": "Generated STL is missing. Generate the cutter again.",
        "fr": "Le fichier STL généré est introuvable. Générez l'emporte-pièce à nouveau.",
        "ru": "Сгенерированный STL не найден. Сгенерируйте формочку заново.",
    },
    "save_failed": {
        "en": "Could not save to library: {error}",
        "fr": "Impossible d'enregistrer dans la bibliothèque : {error}",
        "ru": "Не удалось сохранить в библиотеку: {error}",
    },
    "delete_forbidden": {
        "en": "Only the person who saved this cutter can delete it.",
        "fr": "Seule la personne qui a enregistré cet emporte-pièce peut le supprimer.",
        "ru": "Удалить формочку может только тот, кто её сохранил.",
    },
    # Print plate
    "unknown_printer": {
        "en": "Unknown printer model: {printer}",
        "fr": "Modèle d'imprimante inconnu : {printer}",
        "ru": "Неизвестная модель принтера: {printer}",
    },
    "quantity_range": {
        "en": "Quantity must be between 1 and {max}.",
        "fr": "La quantité doit être comprise entre 1 et {max}.",
        "ru": "Количество должно быть от 1 до {max}.",
    },
    "plate_stl_missing": {
        "en": "STL for '{name}' is missing.",
        "fr": "Le fichier STL de « {name} » est introuvable.",
        "ru": "Не найден STL для «{name}».",
    },
    "plate_empty": {
        "en": "Select at least one cutter.",
        "fr": "Sélectionnez au moins un emporte-pièce.",
        "ru": "Выберите хотя бы одну формочку.",
    },
    "plate_too_many": {
        "en": "A plate can hold at most {max} cutters (got {count}).",
        "fr": "Un plateau peut contenir au plus {max} emporte-pièces (demandé : {count}).",
        "ru": "На столе помещается не более {max} формочек (выбрано {count}).",
    },
    "plate_no_fit": {
        "en": "These {count} cutters don't fit on the bed together ({percent} of the usable area before spacing). Remove some cutters or lower the quantities.",
        "fr": "Ces {count} emporte-pièces ne tiennent pas ensemble sur le plateau ({percent} de la surface utile, hors espacement). Retirez-en ou réduisez les quantités.",
        "ru": "Эти формочки ({count} шт.) не помещаются на стол вместе ({percent} полезной площади без учёта зазоров). Уберите часть формочек или уменьшите количество.",
    },
    "plate_failed": {
        "en": "Could not build the print plate: {error}",
        "fr": "Impossible de préparer le plateau d'impression : {error}",
        "ru": "Не удалось собрать стол печати: {error}",
    },
}


def translate(key: str, lang: str, **params) -> str:
    texts = MESSAGES.get(key)
    if not texts:
        return key
    template = texts.get(lang) or texts[DEFAULT_LANGUAGE]
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def request_language(request: Request) -> str:
    """The `lang` cookie if set, else the best supported Accept-Language match, else English."""
    cookie = (request.cookies.get(LANG_COOKIE) or "").lower()
    if cookie in SUPPORTED_LANGUAGES:
        return cookie
    header = request.headers.get("accept-language", "")
    weighted = []
    for part in header.split(","):
        pieces = part.strip().split(";q=")
        code = pieces[0].strip().lower().split("-")[0]
        try:
            q = float(pieces[1]) if len(pieces) > 1 else 1.0
        except ValueError:
            q = 0.0
        if code in SUPPORTED_LANGUAGES:
            weighted.append((q, code))
    return max(weighted)[1] if weighted else DEFAULT_LANGUAGE
