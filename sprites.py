
import os
import sys
import array

from os import SEEK_SET
from struct import unpack
from dataclasses import dataclass
from typing import List

from PIL import Image
from PIL.Image import Resampling


@dataclass
class Mode:
    mode: int
    colours: int  # number of colours
    px_width: int  # pixel width in OS units
    px_height: int # pixel height in OS units

    @property
    def ppw(self): # pixels per word
        return COLOURS_TO_PIXELS_PER_WORD[self.colours]

    @property
    def bpp(self): # bits per pixel
        return 32//self.ppw

    
MODES = { m.mode : m for m in (
    Mode(0, colours=2, px_width=2, px_height=4),
    Mode(1, colours=4, px_width=4, px_height=4),
    Mode(4, colours=2, px_width=2, px_height=4),
    Mode(8, colours=4, px_width=2, px_height=4),
    Mode(9, colours=16, px_width=4, px_height=4),
    Mode(12, colours=16, px_width=2, px_height=4),
    Mode(13, colours=256, px_width=4, px_height=4),
    Mode(15, colours=256, px_width=2, px_height=4),
    Mode(18, colours=2, px_width=2, px_height=2),
    Mode(19, colours=4, px_width=2, px_height=2),
    Mode(20, colours=16, px_width=2, px_height=2),
    Mode(21, colours=256, px_width=2, px_height=2),
    Mode(28, colours=256, px_width=2, px_height=2),
)}


WIMP_PALETTE_MODE_12 = (
    0xffffff, 0xdddddd, 0xbbbbbb, 0x999999,
    0x777777, 0x555555, 0x333333, 0x000000,
    0x004499, 0xeeee00, 0x00cc00, 0xdd0000,
    0xeeeebb, 0x558800, 0xffbb00, 0x00bbff
)

# Why is the 16-colour WIMP palette different in 256-colour modes?
WIMP_PALETTE_MODE_15 = (
    0xffffff, 0xdddddd, 0xbbbbbb, 0x999999,
    0x777777, 0x555555, 0x333333, 0x000000,
    0x004488, 0xeeee22, 0x00cc00, 0xcc0000,
    0xeeeeaa, 0x448800, 0xffbb33, 0x22aaee
)


# Map from no. of colours to default WIMP palette
WIMP_PALETTES = {
    2: (0xffffff, 0),
    4: (0xffffff, 0xbbbbbb, 0x777777, 0),
    16: WIMP_PALETTE_MODE_15
}


COLOURS_TO_PIXELS_PER_WORD = {
    2: 32,
    4: 16,
    16: 8,
    256: 4,
}


class SpriteArea:
    def __init__(self, fd):
        self.fd = fd
        self.num_sprites, self.first_sprite_offset, self.next_free_word = unpack('<III', fd.read(12))
        self._sprite_offsets = None 
    
    def __str__(self):
        return f'SpriteArea(num_sprites={self.num_sprites} next_free=0x{self.next_free_word:x})'

    def sprites(self):
        offset = self.first_sprite_offset - 4
        self.fd.seek(offset)
        while offset < self.next_free_word - 12:
            next_sprite_offset = int.from_bytes(self.fd.read(4), 'little')
            yield Sprite(self.fd)
            offset += next_sprite_offset
            self.fd.seek(offset, SEEK_SET)

    def __getitem__(self, name) -> 'Sprite':
        if not self._sprite_offsets:
            self._sprite_offsets = {s.name : s.file_offset for s in self.sprites()}
        self.fd.seek(self._sprite_offsets[name], SEEK_SET)
        return Sprite(self.fd)
         

class PaletteEntry:
    def __init__(self, val):
        self.val = val

    @property
    def r(self):
        return (self.rgb >> 16) & 0xff

    @property
    def g(self):
        return (self.rgb >> 8) & 0xff

    @property
    def b(self):
        return self.rgb & 0xff

    @property
    def rgb(self):
        bgr = self.val & 0xffffffff
        r = (bgr >> 8) & 0xff
        g = (bgr >> 16) & 0xff
        b = bgr >> 24
        return r << 16 | g << 8 | b
        
    def __str__(self):
        return f'({self.val:016x} {self.rgb:06x})'
        

class Palette:
    def __init__(self, data):
        self.palette = array.array('Q', data)

    def __len__(self):
        return len(self.palette)

    def __getitem__(self, n):
        return PaletteEntry(self.palette[n])


class Sprite:
    PALETTE_OFFSET = 44

    def __init__(self, fd):
        self.fd = fd
        self.file_offset = fd.tell()
        self.name = fd.read(12).rstrip(b'\x00').decode('iso8859-1')
        width_words, height, \
            row_first_bit, row_last_bit, \
            self.img_offset, self.mask_offset, self.mode = unpack('<IIIIIII', self.fd.read(4*7))

        self.width_words = width_words + 1
        pixel_width = self.width_words * self.mode_info.ppw
        self.rtrim = 0
        self.ltrim = 0
        if row_last_bit:
            self.rtrim = (31 - row_last_bit)//self.mode_info.bpp
        if row_first_bit:
            raise NotImplementedError()
            self.ltrim = row_first_bit // self.mode_info.bpp 
        self.width = pixel_width - self.rtrim - self.ltrim
        self.height = height + 1
        
    @property
    def mode_info(self):
        try:
            return MODES[self.mode]
        except KeyError:
            raise Exception(f'No mode info for mode {self.mode}')

    @property
    def palette_size(self) -> int:
        return (min(self.img_offset,self.mask_offset)-Sprite.PALETTE_OFFSET)//8

    @property
    def has_palette(self) -> bool:
        return self.palette_size > 0

    @property
    def has_mask(self) -> bool:
        return self.img_offset != self.mask_offset

    @property
    def palette(self):
        if not self.has_palette:
            raise RuntimeError('Sprite does not have palette')
        return Palette(self.palette_data_raw)

    def __str__(self):
        attrs = ''
        if self.has_mask:
            attrs += ' mask'
        if self.has_palette:
            attrs += f' palette({self.palette_size})'
        return f'Sprite(name={self.name} mode={self.mode}{attrs} w={self.width} h={self.height})'

    @property
    def palette_data_raw(self):
        if not self.has_palette:
            return None
        self.fd.seek(self.file_offset + Sprite.PALETTE_OFFSET - 4, SEEK_SET)
        return self.fd.read(8*self.palette_size)

    @property
    def pixel_data_raw(self):
        self.fd.seek(self.file_offset + self.img_offset - 4, SEEK_SET)
        return self.fd.read(self.width_words * 4 * self.height)

    @property
    def mask_data_raw(self):
        if not self.has_mask:
            return None
        self.fd.seek(self.file_offset + self.mask_offset - 4, SEEK_SET)
        return self.fd.read(self.width_words * 4 * self.height)

    def _raw_to_bytearray(self, raw_data: bytes):
        """
        Convert the raw sprite data to a 1-byte per pixel bytearray
        """
        data = array.array('I', raw_data)
        bpp = self.mode_info.bpp
        ppw = self.mode_info.ppw
        pixel_mask = 2**bpp - 1
        max_x = self.width_words * ppw - self.rtrim
        out_data = bytearray(self.width * self.height)

        for i, word in enumerate(data):
            y = i // self.width_words
            wx = i % self.width_words
            for j in range(ppw):
                x = wx*ppw + j
                if x >= max_x:
                    continue
                pixel_val = (word >> (j*bpp)) & pixel_mask
                out_data[y*self.width + x] = pixel_val
        return out_data

    @property
    def pixel_bytes(self):
        return self._raw_to_bytearray(self.pixel_data_raw)

    @property
    def mask_bytes(self):
        return self._raw_to_bytearray(self.mask_data_raw)   

def palette_64_to_rgb(palette: Palette):
    pal = [c.rgb for c in palette]
    for j in range(64, 256, 64):
        for i in range(0,64):
            c = palette[i]
            r = (((j + i) & 0x10) >> 1) | (c.r >> 4)
            g = (((j + i) & 0x40) >> 3) | \
                (((j + i) & 0x20) >> 3) | (c.g >> 4)
            b = (((j + i) & 0x80) >> 4) | (c.b >> 4)
            val = ((r + (r << 4)) << 16) | ((g + (g << 4)) << 8) | (b + (b << 4))
            pal.append(val)
    return pal

def get_rgb_palette(sprite: Sprite) -> List[int]:
    if sprite.has_palette:
        if sprite.mode_info.colours < 256:
            assert len(sprite.palette) == sprite.mode_info.colours
            pal = [c.rgb for c in sprite.palette]
        elif len(sprite.palette) == 64:
            pal = palette_64_to_rgb(sprite.palette)
        elif len(sprite.palette) == 16:
            raise NotImplementedError()
        else:
            raise ValueError(f'Unexpected number of colours in palette: {len(sprite.palette)}')
    else:
        colours = MODES[sprite.mode].colours
        pal = WIMP_PALETTES[colours]
    return pal

   
def pil_image(sprite: Sprite) -> Image:
    pal = get_rgb_palette(sprite)
    pixel_data = bytearray(spr.width * spr.height)
    img = spr.pixel_bytes
    mask = None
    if sprite.has_mask:
        mask = spr.mask_bytes
    
    alpha = 0xff
    for i in range(len(pixel_data)):
        if mask:
            alpha = 0xff if mask[i] else 0
        val = (pal[img[i]] << 8) | alpha
        pixel_data[i*4:i*4+4] = val.to_bytes(4, 'big')   
    img = Image.frombytes('RGBA', (spr.width, spr.height), pixel_data)
   
    if sprite.mode_info.px_height > sprite.mode_info.px_width:
        img = img.resize((img.width, img.height * 2), Resampling.NEAREST)

    return img
           

if __name__ == '__main__':
    with open(sys.argv[1], 'rb') as f:
        sprite_area = SpriteArea(f)
        print(sprite_area)
        os.makedirs('sprites', exist_ok=True)
        for spr in sprite_area.sprites():
            print(spr)
            img = pil_image(spr)
            img.save(f'sprites/{spr.name}.png')

