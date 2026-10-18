"""Append visually verified real game-font digits to the offline timer bank."""
from pathlib import Path
import argparse
import re
import numpy as np

from queue_monitor import text_glyphs, normalize_glyph
from yolo_dataset import read_image


def calibrate(image, box, text, bank):
    if not re.fullmatch(r"\d{2}:[0-5]\d:[0-5]\d", text):
        raise ValueError("请输入已从图像人工核验的 HH:MM:SS")
    frame = read_image(image)
    x0,y0,x1,y1 = box
    if frame is None or not (0<=x0<x1<=frame.shape[1] and 0<=y0<y1<=frame.shape[0]):
        raise ValueError("时间区域无效")
    glyphs = text_glyphs(frame[y0:y1,x0:x1])
    if len(glyphs) != 8:
        raise ValueError("时间区域未分离出8个字符，不能写入模板")
    with np.load(bank,allow_pickle=False) as data:
        samples,labels = data["samples"].copy(),data["labels"].copy()
    added = [normalize_glyph(g) for char,g in zip(text,glyphs) if char.isdigit()]
    added_labels = [int(char) for char in text if char.isdigit()]
    samples=np.concatenate([samples,np.array(added,dtype=np.float32)])
    labels=np.concatenate([labels,np.array(added_labels,dtype=labels.dtype)])
    # Exact duplicates add no information and should not inflate the bank.
    keep=[];seen=set()
    for i,(sample,label) in enumerate(zip(samples,labels)):
        key=(int(label),sample.tobytes())
        if key not in seen: keep.append(i);seen.add(key)
    np.savez_compressed(bank,samples=samples[keep],labels=labels[keep])
    return len(keep)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image",type=Path)
    parser.add_argument("--box",nargs=4,type=int,required=True)
    parser.add_argument("--text",required=True)
    parser.add_argument("--bank",type=Path,default=Path("templates/timer_digits.npz"))
    args=parser.parse_args()
    print(calibrate(args.image,args.box,args.text,args.bank))
