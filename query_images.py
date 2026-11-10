"""Bound and normalize optional query images locally; no inference or persistence."""
import base64
import binascii
import io

MAX_IMAGE_BYTES = 3 * 1024**2
MAX_PIXELS = 16_000_000


def normalize_query_images(values, maximum):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(values) > maximum:
        raise ValueError(f'El límite actual es {maximum} imágenes por consulta. Revísalo en Configuración.')
    result=[]
    for value in values:
        encoded=value
        if value.startswith('data:'):
            header, separator, encoded=value.partition(',')
            if not separator or header not in {'data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64'}:
                raise ValueError('Formato de imagen no compatible. Usa PNG, JPG o WEBP.')
        try:
            data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error) as error:
            raise ValueError('La imagen adjunta no contiene datos base64 válidos.') from error
        if not data or len(data)>MAX_IMAGE_BYTES:
            raise ValueError('Cada imagen adjunta debe ocupar como máximo 3 MB.')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height>MAX_PIXELS:
                    raise ValueError('Usa una imagen PNG, JPG o WEBP de hasta 16 megapíxeles.')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                normalized=ImageOps.exif_transpose(image).convert('RGB')
                normalized.thumbnail((1440,1440),Image.Resampling.LANCZOS)
                output=io.BytesIO();normalized.save(output,format='JPEG',quality=92)
                result.append(base64.b64encode(output.getvalue()).decode('ascii'))
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
            raise ValueError('No se ha podido leer la imagen adjunta.') from error
    return result
