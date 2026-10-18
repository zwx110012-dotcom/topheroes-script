"""Local screenshot import, draft annotation and manual box review for Top Heroes."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import native_runtime
import cv2
import numpy as np
import yaml

from yolo_detector import CLASS_NAMES, NAVIGATION_ROIS

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "datasets/topheroes"


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def save_boxes(path, boxes, width, height):
    lines = []
    for name, x0, y0, x1, y1 in boxes:
        if name not in CLASS_NAMES or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError(f"无效标注：{name} {x0,y0,x1,y1}")
        lines.append(f"{CLASS_NAMES.index(name)} {(x0+x1)/2/width:.8f} {(y0+y1)/2/height:.8f} "
                     f"{(x1-x0)/width:.8f} {(y1-y0)/height:.8f}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_boxes(path, width, height):
    boxes = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            cls, cx, cy, w, h = map(float, line.split())
            boxes.append([CLASS_NAMES[int(cls)], round((cx-w/2)*width), round((cy-h/2)*height),
                          round((cx+w/2)*width), round((cy+h/2)*height)])
    return boxes


def propose(frame):
    from crystal_gather import find_matches, read_gather_config
    from top_heroes import load_template
    from queue_monitor import QueueReader
    config = read_gather_config(ROOT / "gather_crystal.yaml")
    templates = {name: load_template(ROOT / spec["path"]) for name, spec in config["templates"].items()}
    boxes = []
    for name, spec in config["templates"].items():
        matches = find_matches(frame, templates[name], config["scales"],
                               float(spec.get("confidence", config["confidence"])),
                               spec.get("roi") if frame.shape[1] >= 1000 else None,
                               float(spec.get("blur_sigma", 0)))
        for m in matches[:4 if name == "idle" else 1]:
            boxes.append([name, m.x, m.y, m.x+m.width, m.y+m.height])
    headers = find_matches(frame, load_template(ROOT / "templates/queue_header.png"),
                           [1], 0.9, None, 0.5)
    if len(headers) == 1:
        m = headers[0]
        boxes.append(["queue_header", m.x, m.y, m.x+m.width, m.y+m.height])
        rows = QueueReader(ROOT, templates["idle"], capacity=4).read(frame, [0, 0, 1, 1])
        if rows:
            for i, row in enumerate(rows):
                y = m.y + 30 + 36*i
                if row.state == "idle":
                    continue
                if row.state in ("mining", "outbound", "returning"):
                    kind = "queue_returning" if row.state == "returning" else "queue_mining"
                    boxes.append([kind, m.x+3, y+7, m.x+29, y+31])
                if row.state == "outbound":
                    boxes.append(["queue_fast_forward", m.x+104, y, m.x+139, y+31])
                if row.text:
                    boxes.append(["timer_region", m.x+34, y, m.x+104, y+25])
    return boxes


def import_images(dataset, sources):
    images, labels = dataset / "images", dataset / "labels"
    images.mkdir(parents=True, exist_ok=True); labels.mkdir(exist_ok=True)
    manifest_path = dataset / "review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    known = {item["sha256"] for item in manifest.values()}
    for source in sources:
        paths = [source] if source.is_file() else sorted(source.glob("*.png"))
        for path in paths:
            frame = read_image(path)
            if path.stem == "template_sheet" or frame is None or frame.shape[1] < 120 or frame.shape[0] < 80:
                continue  # Button crops are not whole-scene training examples.
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
            if digest in known:
                continue
            name = f"{path.stem}-{digest[:10]}.png"
            ok, buf = cv2.imencode(".png", frame)
            if not ok:
                raise ValueError(f"截图无法编码：{path}")
            buf.tofile(str(images/name))
            boxes = propose(frame)
            save_boxes(labels / (Path(name).stem + ".txt"), boxes, frame.shape[1], frame.shape[0])
            manifest[name] = {"sha256": digest, "source": str(path.resolve()), "reviewed": False}
            known.add(digest)
            print(f"草稿 {name}: {len(boxes)} 框，待人工复核", flush=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def augment_navigation(dataset):
    """Create navigation crops from reviewed training parents only."""
    make_split(dataset)
    manifest_path=dataset/'review.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    training={Path(p).name for p in (dataset/'train.txt').read_text(encoding='utf-8').splitlines()}
    validation={Path(p).name for p in (dataset/'val.txt').read_text(encoding='utf-8').splitlines()}
    # Pin the original split before adding derived train images, so an implicit
    # split cannot later turn its validation parents into training examples.
    for name,meta in manifest.items():
        if name in training:meta['split']='train'
        elif name in validation:meta['split']='val'
    added=0
    for name,meta in list(manifest.items()):
        if not meta.get('reviewed') or name not in training or meta.get('parent'):
            continue
        frame=read_image(dataset/'images'/name);h,w=frame.shape[:2]
        if w<1000 or h<800:
            continue
        boxes=load_boxes(dataset/'labels'/(Path(name).stem+'.txt'),w,h)
        for kind,roi in NAVIGATION_ROIS.items():
            x0,y0,x1,y1=[round(v*d) for v,d in zip(roi,[w,h,w,h])]
            crop=frame[y0:y1,x0:x1]
            cropped=[[label,ax-x0,ay-y0,bx-x0,by-y0] for label,ax,ay,bx,by in boxes
                     if x0<=ax<bx<=x1 and y0<=ay<by<=y1]
            child=f'navigation-{kind}-{name}'
            ok,buf=cv2.imencode('.png',crop)
            if not ok:raise ValueError('导航裁片编码失败')
            buf.tofile(str(dataset/'images'/child))
            save_boxes(dataset/'labels'/(Path(child).stem+'.txt'),cropped,x1-x0,y1-y0)
            manifest[child]={'sha256':hashlib.sha256(crop.tobytes()).hexdigest(),
                'source':meta['source'],'reviewed':True,'split':'train','parent':name,
                'sample_type':'derived_navigation_roi','roi':roi,
                'note':'Training-only derivative; never an independent validation sample.'}
            added+=1
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return added


def make_split(dataset, bootstrap=False):
    manifest = json.loads((dataset / "review.json").read_text(encoding="utf-8"))
    names = sorted(name for name, data in manifest.items()
                   if not data.get("exclude_from_training") and not name.startswith("template_sheet-")
                   and data.get("split") != "test" and (bootstrap or data.get("reviewed")))
    if len(names) < 5:
        raise ValueError("至少需要5张完整截图；正式训练只使用已人工复核的标注")
    # This seed split is development only; independent session holdout is needed for deployment.
    explicit = any(manifest[name].get("split") in ("train", "val") for name in names)
    if explicit:
        val = [name for name in names if manifest[name].get("split") == "val"]
        train = [name for name in names if name not in val]
    else:
        val = names[::5]; train = [name for name in names if name not in val]
    # A singleton target must remain in training; a val-only class cannot be learned.
    for name in val.copy():
        classes = {int(line.split()[0]) for line in
                   (dataset/"labels"/(Path(name).stem+".txt")).read_text().splitlines()}
        train_classes = {int(line.split()[0]) for item in train for line in
                         (dataset/"labels"/(Path(item).stem+".txt")).read_text().splitlines()}
        if classes - train_classes:
            val.remove(name); train.append(name)
    if not val:
        raise ValueError("需要独立验证样本；不能把某类唯一的图片全分到验证集")
    for split, selected in [("train", train), ("val", val)]:
        (dataset / f"{split}.txt").write_text("\n".join(str((dataset/"images"/name).resolve()) for name in selected)+"\n", encoding="utf-8")
    config = {"path": str(dataset.resolve()), "train": "train.txt", "val": "val.txt",
              "names": dict(enumerate(CLASS_NAMES))}
    (dataset / "data.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return config


def review_ui(dataset):
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk
    manifest_path = dataset / "review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = sorted(manifest, key=lambda name: manifest[name].get("reviewed", False))
    if not names:
        raise ValueError("请先导入截图")
    root = tk.Tk(); root.title("Top Heroes · YOLO 标注复核"); root.geometry("1240x850")
    kind = tk.StringVar(value=CLASS_NAMES[0]); status = tk.StringVar()
    bar = ttk.Frame(root); bar.pack(fill="x")
    ttk.Combobox(bar, textvariable=kind, values=CLASS_NAMES, state="readonly", width=24).pack(side="left")
    state = {"index": 0, "boxes": [], "scale": 1, "origin": None}
    canvas = tk.Canvas(root, bg="#111827", width=1200, height=680); canvas.pack(fill="both", expand=True)
    ttk.Label(root, textvariable=status).pack(fill="x")
    ttk.Label(root, text="选类别后拖动新增框；右键删除框；保存复核会确认所有可见目标已完整标注。漏标目标会被训练成背景。", wraplength=1200).pack(fill="x")
    def draw():
        canvas.delete("all")
        canvas.create_image(0, 0, image=state["photo"], anchor="nw")
        scale = state["scale"]
        for name, x0, y0, x1, y1 in state["boxes"]:
            canvas.create_rectangle(x0*scale, y0*scale, x1*scale, y1*scale, outline="#61e8aa", width=2)
            canvas.create_text(x0*scale, max(10,y0*scale), text=name, fill="yellow", anchor="sw")
    def load():
        name = names[state["index"]]
        frame = read_image(dataset/"images"/name)
        h,w = frame.shape[:2]; state.update(width=w, height=h)
        state["scale"] = min(1200/w, 680/h)
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image = image.resize((round(w*state["scale"]), round(h*state["scale"])))
        state["photo"] = ImageTk.PhotoImage(image)
        state["boxes"] = load_boxes(dataset/"labels"/(Path(name).stem+".txt"),w,h)
        status.set(f"{state['index']+1}/{len(names)} · {name} · {'已复核' if manifest[name]['reviewed'] else '自动草稿，待复核'}")
        draw()
    def save(reviewed):
        name = names[state["index"]]
        save_boxes(dataset/"labels"/(Path(name).stem+".txt"),state["boxes"],state["width"],state["height"])
        manifest[name]["reviewed"] = reviewed
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        status.set(f"已保存：{name} · {'已复核' if reviewed else '草稿'}")
    def navigate(delta):
        save(manifest[names[state["index"]]]["reviewed"])
        state["index"] = (state["index"]+delta) % len(names); load()
    def finish_review():
        save(True)
        state["index"] = (state["index"]+1) % len(names); load()
    def down(event):
        state["origin"]=(event.x,event.y)
    def up(event):
        if state["origin"] is None: return
        ax,ay=state.pop("origin"); state["origin"]=None
        scale=state["scale"]
        x0,x1=sorted((max(0,min(state["width"],round(ax/scale))),max(0,min(state["width"],round(event.x/scale)))))
        y0,y1=sorted((max(0,min(state["height"],round(ay/scale))),max(0,min(state["height"],round(event.y/scale)))))
        if x1-x0>=3 and y1-y0>=3: state["boxes"].append([kind.get(),x0,y0,x1,y1])
        manifest[names[state["index"]]]["reviewed"] = False
        draw()
    def delete(event):
        x,y=event.x/state["scale"],event.y/state["scale"]
        for i in range(len(state["boxes"])-1,-1,-1):
            _,x0,y0,x1,y1=state["boxes"][i]
            if x0<=x<=x1 and y0<=y<=y1:
                state["boxes"].pop(i)
                manifest[names[state["index"]]]["reviewed"] = False
                break
        draw()
    canvas.bind("<Button-1>",down); canvas.bind("<ButtonRelease-1>",up); canvas.bind("<Button-3>",delete)
    ttk.Button(bar,text="上一张（保存草稿）",command=lambda:navigate(-1)).pack(side="left")
    ttk.Button(bar,text="下一张（保存草稿）",command=lambda:navigate(1)).pack(side="left")
    ttk.Button(bar,text="保存复核并下一张",command=finish_review).pack(side="left")
    root.protocol("WM_DELETE_WINDOW",lambda:(save(manifest[names[state['index']]]['reviewed']),root.destroy()))
    load(); root.mainloop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["import","review","split","augment-navigation"])
    parser.add_argument("--dataset",type=Path,default=DEFAULT_DATA)
    parser.add_argument("--sources",type=Path,nargs="*")
    parser.add_argument("--bootstrap",action="store_true")
    args=parser.parse_args()
    if args.command=="import":
        import_images(args.dataset,args.sources or [ROOT/"captures",ROOT/"tests/fixtures"])
    elif args.command=="review": review_ui(args.dataset)
    elif args.command=="augment-navigation": print('导航训练裁片:',augment_navigation(args.dataset))
    else: make_split(args.dataset,args.bootstrap)


if __name__=="__main__": main()
