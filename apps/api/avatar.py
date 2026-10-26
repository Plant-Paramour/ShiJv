import io
import threading
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_EDGE = 4096
MAX_AVATAR_PIXELS = 4000000
_DECODERS = threading.BoundedSemaphore(2)

class AvatarBusy(RuntimeError):
    pass

def compress_avatar_bounded(database, content):
    return compress_avatar(content)

def compress_avatar(content: bytes) -> bytes:
    if not content or len(content) > MAX_AVATAR_BYTES:
        raise ValueError('头像大小必须在 2MB 以内')
    if not _DECODERS.acquire(blocking=False):
        raise AvatarBusy('头像处理繁忙，请稍后再试')
    try:
        return _compress_avatar(content)
    finally:
        _DECODERS.release()

def _compress_avatar(content: bytes) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(content), formats=('JPEG', 'PNG', 'WEBP')) as original:
        width, height = original.size
        if width < 1 or height < 1 or max(width, height) > MAX_AVATAR_EDGE or (width * height > MAX_AVATAR_PIXELS):
            raise ValueError('头像边长不能超过 4096，像素总数不能超过 400 万')
        if getattr(original, 'n_frames', 1) != 1:
            raise ValueError('头像必须为静态图片')
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
        for quality in (88, 75, 60, 45, 30):
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            if output.tell() <= 64 * 1024:
                return output.getvalue()
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=45, optimize=True)
        if output.tell() > 64 * 1024:
            raise ValueError('头像压缩失败')
        return output.getvalue()
