import { Plus, Minus, Scan, Crosshair } from "lucide-react";

export default function StudioToolbar({ onZoomIn, onZoomOut, onFit, onFocus, focusDisabled }: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onFocus: () => void;
  focusDisabled: boolean;
}) {
  const btn = "studio-tool-btn";
  return (
    <div className="studio-toolbar">
      <button className={btn} title="放大" onClick={onZoomIn}><Plus size={15} /></button>
      <button className={btn} title="缩小" onClick={onZoomOut}><Minus size={15} /></button>
      <button className={btn} title="适应窗口" onClick={onFit}><Scan size={15} /></button>
      <button className={btn} title="聚焦所选员工" onClick={onFocus} disabled={focusDisabled}><Crosshair size={15} /></button>
    </div>
  );
}
