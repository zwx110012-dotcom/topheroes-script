"""Train game UI detector locally and export a preview-only ONNX model."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from yolo_dataset import DEFAULT_DATA, ROOT, make_split, augment_navigation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,default=DEFAULT_DATA)
    parser.add_argument("--model",type=Path,default=ROOT/"models/yolo26s.pt")
    parser.add_argument("--epochs",type=int,default=80)
    parser.add_argument("--imgsz",type=int,default=960)
    parser.add_argument("--batch",type=int,default=4)
    parser.add_argument("--device",default="0")
    parser.add_argument("--output",type=Path,default=ROOT/"models/topheroes.onnx")
    parser.add_argument("--export-imgsz",type=int,default=960)
    parser.add_argument("--bootstrap",action="store_true",help="Use unreviewed draft labels for a deployment smoke test only")
    args=parser.parse_args()
    if args.epochs<1 or args.batch<1 or args.imgsz<320 or args.imgsz%32:
        raise ValueError("epochs/batch 必须为正数；imgsz 至少320且为32的倍数")
    if not args.bootstrap:
        augment_navigation(args.dataset)
    make_split(args.dataset,args.bootstrap)
    from ultralytics import YOLO, settings
    # Keep game captures and training metrics local.
    settings.update({key: False for key in ("sync", "hub", "wandb", "comet", "clearml", "mlflow")
                     if key in settings})
    args.model.parent.mkdir(parents=True,exist_ok=True)
    model=YOLO(str(args.model))
    # UI text/icons must not be mirrored, rotated, or recolored unrealistically.
    model.train(data=str(args.dataset/"data.yaml"),epochs=args.epochs,imgsz=args.imgsz,
                      batch=args.batch,device=args.device,workers=0,project=str(ROOT/"runs"),
                      name="topheroes",exist_ok=False,seed=42,deterministic=True,
                      optimizer="AdamW",lr0=0.001,nbs=16,warmup_epochs=1,cos_lr=True,
                      fliplr=0,flipud=0,degrees=0,shear=0,perspective=0,
                      hsv_h=0,hsv_s=0,hsv_v=0.08,mosaic=0,mixup=0,scale=0.25,
                      plots=False,cache=False,amp=True)
    save_dir = Path(model.trainer.save_dir)
    best=save_dir/"weights/best.pt"
    trained=YOLO(str(best))
    exported=Path(trained.export(format="onnx",imgsz=args.export_imgsz,batch=1,dynamic=False,
                                simplify=False,half=False,opset=17,nms=None,device="cpu"))
    target=args.output; target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(exported,target)
    info={"stage":"bootstrap" if args.bootstrap else "unverified", "sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
          "base_model":args.model.name,"training_run":str(save_dir),
          "note":"Development validation is not an independent deployment test; preview only until verified."}
    if not args.bootstrap:
        info['navigation_roi_version']=1
    target.with_suffix(".json").write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"已导出预览模型：{target}；需要独立游戏截图验证后才能启用点击",flush=True)


if __name__=="__main__": main()
