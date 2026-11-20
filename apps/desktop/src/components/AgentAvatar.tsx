import { useId, type CSSProperties } from "react";
import {
  FEMALE_HAIRS, MALE_HAIRS, OUTFITS, SKIN_TONES, hairDef, outfitDef, skinBase,
  type AgentAppearance, type HairStyleKind,
} from "../lib/appearance";

/* Original flat vector avatars, drawn entirely in code — no third-party
 * artwork, no downloaded images.
 *
 * Two rendering paths:
 * 1. Configurable appearance (current): an AgentAppearance config —
 *    gender / skin 1-5 / outfit 1-10 / hair 1-6 — rendered layer by layer.
 *    The same config drives the pixel studio sprites; the contract lives
 *    in src/lib/appearance.ts and docs/APPEARANCE.md.
 * 2. Legacy presets: each preset combines a hairstyle, hair color, skin
 *    tone, a top in the shared 8-color category palette and an optional
 *    accessory (glasses / headphones / tie). The preset id is what older
 *    rows store in the agent's avatar field; anything else in that field
 *    falls back to the monogram block in components/ui. Agents with an
 *    appearance config always render through path 1. */

export interface AvatarPreset {
  id: string;
  label: string;
  skin: string;
  hairStyle: "long" | "bob" | "bun" | "ponytail" | "short" | "sidepart" | "buzz" | "curly";
  hairColor: string;
  top: string;
  glasses?: boolean;
  headphones?: boolean;
  tie?: boolean;
}

export const AVATAR_PRESETS: AvatarPreset[] = [
  { id: "avatar-01", label: "长发 · 蓝衫", skin: "#F8DCBE", hairStyle: "long", hairColor: "#2E2A26", top: "#3978C6" },
  { id: "avatar-02", label: "波波头 · 紫衫眼镜", skin: "#F3CFA4", hairStyle: "bob", hairColor: "#5A3A22", top: "#7C5CBF", glasses: true },
  { id: "avatar-03", label: "丸子头 · 绿衫", skin: "#F8DCBE", hairStyle: "bun", hairColor: "#2E2A26", top: "#238B61" },
  { id: "avatar-04", label: "马尾 · 玫红衫", skin: "#DFA96F", hairStyle: "ponytail", hairColor: "#3A2A1E", top: "#C6507B" },
  { id: "avatar-05", label: "短发耳机 · 青衫", skin: "#F3CFA4", hairStyle: "short", hairColor: "#23201D", top: "#1E8A7A", headphones: true },
  { id: "avatar-06", label: "分头眼镜 · 蓝衫", skin: "#F8DCBE", hairStyle: "sidepart", hairColor: "#4A3226", top: "#3978C6", glasses: true },
  { id: "avatar-07", label: "寸头 · 橙衫", skin: "#B07B4F", hairStyle: "buzz", hairColor: "#1F1B18", top: "#D97B29" },
  { id: "avatar-08", label: "卷发 · 金衫", skin: "#8D5A33", hairStyle: "curly", hairColor: "#3A2417", top: "#C58A32" },
  { id: "avatar-09", label: "银灰短发 · 陶土衫", skin: "#F3CFA4", hairStyle: "short", hairColor: "#B9BDC4", top: "#C2703D", glasses: true },
  { id: "avatar-10", label: "波浪金发 · 玫红衫", skin: "#F8DCBE", hairStyle: "long", hairColor: "#D9A441", top: "#C6507B" },
  { id: "avatar-11", label: "长发 · 绿衫", skin: "#F3CFA4", hairStyle: "long", hairColor: "#7A3B1E", top: "#238B61" },
  { id: "avatar-12", label: "银灰波波头 · 紫衫", skin: "#F8DCBE", hairStyle: "bob", hairColor: "#C9CDD4", top: "#7C5CBF" },
  { id: "avatar-13", label: "正装领带 · 墨色", skin: "#F3CFA4", hairStyle: "sidepart", hairColor: "#35322E", top: "#242424", tie: true },
  { id: "avatar-14", label: "短发耳机 · 橙衫", skin: "#DFA96F", hairStyle: "short", hairColor: "#2E2A26", top: "#D97B29", headphones: true, glasses: true },
];

const PRESET_BY_ID = new Map(AVATAR_PRESETS.map((p) => [p.id, p]));

export function isAvatarPreset(value?: string | null): boolean {
  return !!value && PRESET_BY_ID.has(value);
}

function HairBack({ p }: { p: AvatarPreset }) {
  return <HairBackShape style={p.hairStyle} color={p.hairColor} tieColor={p.top} />;
}

/** Hair mass behind the head (long styles / ponytail), shared by the
 * preset renderer and the appearance renderer. */
function HairBackShape({ style, color, tieColor }: { style: HairStyleKind; color: string; tieColor: string }) {
  if (style === "long") {
    return <rect x="18.5" y="10" width="27" height="42" rx="11" fill={color} />;
  }
  if (style === "bob") {
    return <rect x="19" y="10" width="26" height="31" rx="10" fill={color} />;
  }
  if (style === "ponytail") {
    return (
      <>
        <ellipse cx="46.5" cy="28" rx="3.6" ry="10.5" fill={color} />
        <circle cx="43.4" cy="16.5" r="2.3" fill={tieColor} />
      </>
    );
  }
  return null;
}

function HairTop({ p }: { p: AvatarPreset }) {
  return <HairTopShape style={p.hairStyle} color={p.hairColor} />;
}

function HairTopShape({ style, color: c }: { style: HairStyleKind; color: string }) {
  switch (style) {
    case "buzz":
      return <path d="M21.3 25.5 C21 15.8 25.4 11 32 11 C38.6 11 43 15.8 42.7 25.5 C40.8 19.6 37 17.9 32 17.9 C27 17.9 23.2 19.6 21.3 25.5 Z" fill={c} />;
    case "sidepart":
      return <path d="M20.9 28.5 C20.2 16.5 25 10.8 32 10.8 C39 10.8 43.8 16.5 43.1 28.5 C41.8 21.3 38.4 19.3 33.4 19.1 C26.8 18.8 22.9 21.1 20.9 28.5 Z" fill={c} />;
    case "curly":
      return (
        <>
          <path d="M20.9 28.5 C20.2 16.5 25 10.8 32 10.8 C39 10.8 43.8 16.5 43.1 28.5 C41.5 20.8 37.8 18.6 32 18.6 C26.2 18.6 22.5 20.8 20.9 28.5 Z" fill={c} />
          <circle cx="23.5" cy="19.8" r="2.8" fill={c} />
          <circle cx="27.5" cy="18.2" r="3" fill={c} />
          <circle cx="32" cy="17.6" r="3.1" fill={c} />
          <circle cx="36.5" cy="18.2" r="3" fill={c} />
          <circle cx="40.5" cy="19.8" r="2.8" fill={c} />
        </>
      );
    case "bun":
      return (
        <>
          <circle cx="32" cy="8.8" r="4.4" fill={c} />
          <path d="M20.9 28.5 C20.2 16.5 25 10.8 32 10.8 C39 10.8 43.8 16.5 43.1 28.5 C41.5 20.8 37.8 18.6 32 18.6 C26.2 18.6 22.5 20.8 20.9 28.5 Z" fill={c} />
        </>
      );
    case "long":
    case "bob":
    case "ponytail":
      // Fringe with a subtle center part.
      return <path d="M20.9 28.5 C20.2 16.5 25 10.8 32 10.8 C39 10.8 43.8 16.5 43.1 28.5 C41.6 21.2 38.2 19 32.7 18.8 L32.7 15.6 L31.3 15.6 L31.3 18.8 C25.8 19 22.4 21.2 20.9 28.5 Z" fill={c} />;
    default: // short
      return <path d="M20.9 28.5 C20.2 16.5 25 10.8 32 10.8 C39 10.8 43.8 16.5 43.1 28.5 C41.5 20.8 37.8 18.6 32 18.6 C26.2 18.6 22.5 20.8 20.9 28.5 Z" fill={c} />;
  }
}

export function AgentAvatarFigure({ presetId, size }: { presetId: string; size: number }) {
  const p = PRESET_BY_ID.get(presetId);
  const clipId = useId();
  if (!p) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ display: "block" }}>
      <clipPath id={clipId}><circle cx="32" cy="32" r="32" /></clipPath>
      <g clipPath={`url(#${clipId})`}>
        <rect x="0" y="0" width="64" height="64" fill={p.top} opacity="0.16" />
        <HairBack p={p} />
        {/* torso */}
        <path d="M12 64 C12.5 52 20 46.5 32 46.5 C44 46.5 51.5 52 52 64 Z" fill={p.top} />
        {p.tie ? (
          <>
            <path d="M27.5 46.6 L32 53.2 L36.5 46.6 L34.9 45.7 L29.1 45.7 Z" fill="#FFFFFF" />
            <path d="M32 49.6 L34.2 52.3 L33.1 59.5 L30.9 59.5 L29.8 52.3 Z" fill="#C58A32" />
          </>
        ) : (
          <path d="M28.6 46.4 L32 50.6 L35.4 46.4 L33.7 45.6 L30.3 45.6 Z" fill={p.skin} />
        )}
        {/* neck + head */}
        <rect x="28.4" y="35" width="7.2" height="10.5" rx="2.5" fill={p.skin} />
        <rect x="28.4" y="35" width="7.2" height="4" fill="#000000" opacity="0.08" />
        <circle cx="20.4" cy="30.5" r="2.7" fill={p.skin} />
        <circle cx="43.6" cy="30.5" r="2.7" fill={p.skin} />
        <ellipse cx="32" cy="29" rx="11.2" ry="12.3" fill={p.skin} />
        <HairTop p={p} />
        {/* face */}
        <ellipse cx="23.6" cy="34.8" rx="2.2" ry="1.3" fill="#E8907C" opacity="0.35" />
        <ellipse cx="40.4" cy="34.8" rx="2.2" ry="1.3" fill="#E8907C" opacity="0.35" />
        <circle cx="27.6" cy="30" r="1.25" fill="#2A2320" />
        <circle cx="36.4" cy="30" r="1.25" fill="#2A2320" />
        <path d="M25.4 26.7 Q27.6 25.5 29.8 26.7" stroke={p.hairColor} strokeWidth="1.1" fill="none" strokeLinecap="round" opacity="0.55" />
        <path d="M34.2 26.7 Q36.4 25.5 38.6 26.7" stroke={p.hairColor} strokeWidth="1.1" fill="none" strokeLinecap="round" opacity="0.55" />
        <path d="M29.2 36.8 Q32 38.8 34.8 36.8" stroke="#A9714B" strokeWidth="1.2" fill="none" strokeLinecap="round" />
        {p.glasses && (
          <g stroke="#3A3A3A" strokeWidth="1.5" fill="#FFFFFF" fillOpacity="0.08">
            <rect x="21.6" y="26.6" width="9.6" height="7.8" rx="2.7" />
            <rect x="32.8" y="26.6" width="9.6" height="7.8" rx="2.7" />
            <path d="M31.2 29.4 Q32 28.7 32.8 29.4" fill="none" />
          </g>
        )}
        {p.headphones && (
          <>
            <path d="M19.6 30.5 C19.6 14.8 44.4 14.8 44.4 30.5" stroke="#33393F" strokeWidth="2.6" fill="none" />
            <rect x="16.7" y="27.5" width="5.2" height="10.5" rx="2.3" fill="#33393F" />
            <rect x="42.1" y="27.5" width="5.2" height="10.5" rx="2.3" fill="#33393F" />
            <circle cx="19.3" cy="32.8" r="1.1" fill={p.top} />
            <circle cx="44.7" cy="32.8" r="1.1" fill={p.top} />
          </>
        )}
      </g>
    </svg>
  );
}

/** Flat avatar rendered from a configurable appearance (gender / skin /
 * outfit / hair, see src/lib/appearance.ts). Layer order mirrors the
 * pixel studio's sprite composition: hair back, torso, head, hair top,
 * face, accessory — so both renderers read the same config the same way. */
export function AppearanceFigure({ appearance, size }: { appearance: AgentAppearance; size: number }) {
  const clipId = useId();
  const skin = skinBase(appearance.skin);
  const hair = hairDef(appearance.gender, appearance.hair);
  const outfit = outfitDef(appearance.outfit);
  const female = appearance.gender === "female";
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ display: "block" }}>
      <clipPath id={clipId}><circle cx="32" cy="32" r="32" /></clipPath>
      <g clipPath={`url(#${clipId})`}>
        <rect x="0" y="0" width="64" height="64" fill={outfit.top} opacity="0.16" />
        <HairBackShape style={hair.style} color={hair.color} tieColor={outfit.top} />
        {/* torso: female silhouette is slightly narrower at the shoulders */}
        {female
          ? <path d="M13.5 64 C14 52.5 20.8 47 32 47 C43.2 47 50 52.5 50.5 64 Z" fill={outfit.top} />
          : <path d="M12 64 C12.5 52 20 46.5 32 46.5 C44 46.5 51.5 52 52 64 Z" fill={outfit.top} />}
        {outfit.collar === "suit" ? (
          <>
            <path d="M27.5 46.6 L32 53.2 L36.5 46.6 L34.9 45.7 L29.1 45.7 Z" fill="#FFFFFF" />
            <path d="M32 49.6 L34.2 52.3 L33.1 59.5 L30.9 59.5 L29.8 52.3 Z" fill={outfit.accent} />
          </>
        ) : outfit.collar === "crew" ? (
          <path d="M24.5 46.8 Q32 51.6 39.5 46.8 L39.5 50.6 Q32 55.4 24.5 50.6 Z" fill={outfit.accent} opacity="0.85" />
        ) : (
          <path d="M28.6 46.4 L32 50.6 L35.4 46.4 L33.7 45.6 L30.3 45.6 Z" fill={skin} />
        )}
        {/* neck + head */}
        <rect x="28.4" y="35" width="7.2" height="10.5" rx="2.5" fill={skin} />
        <rect x="28.4" y="35" width="7.2" height="4" fill="#000000" opacity="0.08" />
        <circle cx="20.4" cy="30.5" r="2.7" fill={skin} />
        <circle cx="43.6" cy="30.5" r="2.7" fill={skin} />
        <ellipse cx="32" cy="29" rx={female ? 10.7 : 11.2} ry="12.3" fill={skin} />
        <HairTopShape style={hair.style} color={hair.color} />
        {/* face */}
        <ellipse cx="23.6" cy="34.8" rx="2.2" ry="1.3" fill="#E8907C" opacity="0.35" />
        <ellipse cx="40.4" cy="34.8" rx="2.2" ry="1.3" fill="#E8907C" opacity="0.35" />
        <circle cx="27.6" cy="30" r="1.25" fill="#2A2320" />
        <circle cx="36.4" cy="30" r="1.25" fill="#2A2320" />
        <path d="M25.4 26.7 Q27.6 25.5 29.8 26.7" stroke={hair.color} strokeWidth="1.1" fill="none" strokeLinecap="round" opacity="0.55" />
        <path d="M34.2 26.7 Q36.4 25.5 38.6 26.7" stroke={hair.color} strokeWidth="1.1" fill="none" strokeLinecap="round" opacity="0.55" />
        <path d="M29.2 36.8 Q32 38.8 34.8 36.8" stroke="#A9714B" strokeWidth="1.2" fill="none" strokeLinecap="round" />
        {outfit.accessory === "glasses" && (
          <g stroke="#3A3A3A" strokeWidth="1.5" fill="#FFFFFF" fillOpacity="0.08">
            <rect x="21.6" y="26.6" width="9.6" height="7.8" rx="2.7" />
            <rect x="32.8" y="26.6" width="9.6" height="7.8" rx="2.7" />
            <path d="M31.2 29.4 Q32 28.7 32.8 29.4" fill="none" />
          </g>
        )}
        {outfit.accessory === "headphones" && (
          <>
            <path d="M19.6 30.5 C19.6 14.8 44.4 14.8 44.4 30.5" stroke="#33393F" strokeWidth="2.6" fill="none" />
            <rect x="16.7" y="27.5" width="5.2" height="10.5" rx="2.3" fill="#33393F" />
            <rect x="42.1" y="27.5" width="5.2" height="10.5" rx="2.3" fill="#33393F" />
            <circle cx="19.3" cy="32.8" r="1.1" fill={outfit.top} />
            <circle cx="44.7" cy="32.8" r="1.1" fill={outfit.top} />
          </>
        )}
      </g>
    </svg>
  );
}

const pickerCell = (selected: boolean): CSSProperties => ({
  display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
  padding: "8px 4px 6px", borderRadius: 10, cursor: "pointer",
  border: selected ? "2px solid var(--info)" : "1px solid var(--border)",
  background: selected ? "var(--info-tint, #E7EFF9)" : "var(--card)",
});
const pickerLabel: CSSProperties = { fontSize: 11, lineHeight: 1.25, textAlign: "center", color: "var(--muted-foreground)" };
const sectionLabel: CSSProperties = { fontSize: 12, fontWeight: 600, margin: "12px 0 6px" };

/** 形象选择器：性别、肤色、发型、装扮四维点选，每格都是用当前配置
 * 实时渲染的真头像，所见即所得；顶部大图为当前组合的预览。选好的
 * 配置同时供像素 AI 工作室渲染同一位员工的像素形象。 */
export function AppearancePicker({ value, onChange }: { value: AgentAppearance; onChange: (a: AgentAppearance) => void }) {
  const hairs = value.gender === "female" ? FEMALE_HAIRS : MALE_HAIRS;
  return (
    <div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <span style={{ borderRadius: "50%", overflow: "hidden", display: "block", width: 88, height: 88, flex: "none", border: "1px solid var(--border)" }}>
          <AppearanceFigure appearance={value} size={88} />
        </span>
        <div>
          <div style={{ display: "flex", gap: 6 }}>
            {([["female", "女性"], ["male", "男性"]] as const).map(([g, label]) => (
              <button
                key={g}
                type="button"
                onClick={() => onChange({ ...value, gender: g, hair: 1 })}
                style={{
                  padding: "6px 16px", borderRadius: 999, cursor: "pointer", fontSize: 13,
                  border: value.gender === g ? "2px solid var(--info)" : "1px solid var(--border)",
                  background: value.gender === g ? "var(--info-tint, #E7EFF9)" : "var(--card)",
                  fontWeight: value.gender === g ? 600 : 400,
                }}
              >{label}</button>
            ))}
          </div>
          <div style={sectionLabel}>肤色</div>
          <div style={{ display: "flex", gap: 8 }}>
            {SKIN_TONES.map((t) => (
              <button
                key={t.value}
                type="button"
                title={t.label}
                onClick={() => onChange({ ...value, skin: t.value })}
                style={{
                  width: 30, height: 30, borderRadius: "50%", cursor: "pointer", background: t.base,
                  border: value.skin === t.value ? "2px solid var(--info)" : "1px solid var(--border)",
                  boxShadow: value.skin === t.value ? "0 0 0 2px var(--card)" : undefined,
                }}
              />
            ))}
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>{SKIN_TONES.find((t) => t.value === value.skin)?.label}</div>
        </div>
      </div>

      <div style={sectionLabel}>发型</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(74px, 1fr))", gap: 8 }}>
        {hairs.map((h) => (
          <button key={h.value} type="button" style={pickerCell(value.hair === h.value)}
            onClick={() => onChange({ ...value, hair: h.value })}>
            <span style={{ borderRadius: "50%", overflow: "hidden", display: "block", width: 40, height: 40 }}>
              <AppearanceFigure appearance={{ ...value, hair: h.value }} size={40} />
            </span>
            <span style={pickerLabel}>{h.label}</span>
          </button>
        ))}
      </div>

      <div style={sectionLabel}>装扮</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(74px, 1fr))", gap: 8 }}>
        {OUTFITS.map((o) => (
          <button key={o.value} type="button" style={pickerCell(value.outfit === o.value)}
            onClick={() => onChange({ ...value, outfit: o.value })}>
            <span style={{ borderRadius: "50%", overflow: "hidden", display: "block", width: 40, height: 40 }}>
              <AppearanceFigure appearance={{ ...value, outfit: o.value }} size={40} />
            </span>
            <span style={pickerLabel}>{o.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Grid picker used by the agent create/edit form: what you see is the
 * exact figure that will render everywhere the shared Avatar is used. */
export function AvatarPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(74px, 1fr))", gap: 8 }}>
      {AVATAR_PRESETS.map((p) => {
        const selected = value === p.id;
        return (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(p.id)}
            title={p.label}
            style={{
              display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
              padding: "8px 4px 6px", borderRadius: 10, cursor: "pointer",
              border: selected ? "2px solid var(--info)" : "1px solid var(--border)",
              background: selected ? "var(--info-tint, #E7EFF9)" : "var(--card)",
            }}
          >
            <span style={{ borderRadius: "50%", overflow: "hidden", display: "block", width: 40, height: 40 }}>
              <AgentAvatarFigure presetId={p.id} size={40} />
            </span>
            <span style={{ fontSize: 11, lineHeight: 1.25, textAlign: "center", color: "var(--muted-foreground)" }}>{p.label}</span>
          </button>
        );
      })}
    </div>
  );
}
