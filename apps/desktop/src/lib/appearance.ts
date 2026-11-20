/* 员工形象配置 — 全应用与像素 AI 工作室共用的唯一契约来源。
 *
 * 契约文档：docs/APPEARANCE.md。字段名与取值范围一经定版不要更改，
 * 像素工作室（features/pixel-studio）直接 import 本文件的类型与常量，
 * 按同一份配置渲染像素精灵。
 *
 * 存储位置：后端 agents.appearance JSON 列，结构为 AgentAppearance。
 * 空对象 / 缺失 = 未设置形象，界面回退到旧的 avatar 预设 ID 渲染。
 */

export type AgentGender = "female" | "male";

export interface AgentAppearance {
  /** 形象性别：female 女性 | male 男性 */
  gender: AgentGender;
  /** 肤色档位：1-5，色值见 SKIN_TONES */
  skin: number;
  /** 装扮编号：1-10，定义见 OUTFITS */
  outfit: number;
  /** 发型编号：1-6，含义按性别区分，见 FEMALE_HAIRS / MALE_HAIRS */
  hair: number;
}

export const DEFAULT_APPEARANCE: AgentAppearance = { gender: "female", skin: 2, outfit: 1, hair: 1 };

export interface SkinTone {
  value: number;
  label: string;
  /** 肤色主色，平面头像与像素精灵共用同一色值 */
  base: string;
}

/** 5 档肤色，浅到深的真实色阶（与既有头像预设用色一致）。 */
export const SKIN_TONES: SkinTone[] = [
  { value: 1, label: "浅白", base: "#F8DCBE" },
  { value: 2, label: "自然", base: "#F3CFA4" },
  { value: 3, label: "小麦", base: "#DFA96F" },
  { value: 4, label: "深麦", base: "#B07B4F" },
  { value: 5, label: "深棕", base: "#8D5A33" },
];

export function skinBase(skin: number): string {
  return SKIN_TONES.find((t) => t.value === skin)?.base ?? SKIN_TONES[1].base;
}

export type HairStyleKind = "long" | "bob" | "bun" | "ponytail" | "short" | "sidepart" | "buzz" | "curly";

export interface HairDef {
  value: number;
  label: string;
  /** 平面头像的绘制款式；像素工作室按此选发型底图大类 */
  style: HairStyleKind;
  /** 发色：由发型固定，保证只凭四个字段就能确定性渲染 */
  color: string;
}

export const FEMALE_HAIRS: HairDef[] = [
  { value: 1, label: "长发", style: "long", color: "#2E2A26" },
  { value: 2, label: "波波头", style: "bob", color: "#5A3A22" },
  { value: 3, label: "丸子头", style: "bun", color: "#2E2A26" },
  { value: 4, label: "马尾", style: "ponytail", color: "#3A2A1E" },
  { value: 5, label: "波浪长发", style: "long", color: "#D9A441" },
  { value: 6, label: "短发", style: "short", color: "#7A3B1E" },
];

export const MALE_HAIRS: HairDef[] = [
  { value: 1, label: "短发", style: "short", color: "#23201D" },
  { value: 2, label: "分头", style: "sidepart", color: "#4A3226" },
  { value: 3, label: "寸头", style: "buzz", color: "#1F1B18" },
  { value: 4, label: "卷发", style: "curly", color: "#3A2417" },
  { value: 5, label: "银灰短发", style: "short", color: "#B9BDC4" },
  { value: 6, label: "正装分头", style: "sidepart", color: "#35322E" },
];

export function hairDef(gender: AgentGender, hair: number): HairDef {
  const list = gender === "female" ? FEMALE_HAIRS : MALE_HAIRS;
  return list.find((h) => h.value === hair) ?? list[0];
}

export type OutfitCollar = "v" | "crew" | "suit";
export type OutfitAccessory = "glasses" | "headphones" | "tie" | null;

export interface OutfitDef {
  value: number;
  label: string;
  /** 上衣主色，取自已定版 8 色分类色板（正装用品牌墨色） */
  top: string;
  /** 搭配色：圆领领口带 / 领带等点缀 */
  accent: string;
  collar: OutfitCollar;
  accessory: OutfitAccessory;
}

/** 10 款装扮：衣色组合、领型、配饰均可辨识区分。 */
export const OUTFITS: OutfitDef[] = [
  { value: 1, label: "蓝色衬衫", top: "#3978C6", accent: "#E7EFF9", collar: "v", accessory: null },
  { value: 2, label: "绿色圆领", top: "#238B61", accent: "#E3F2EC", collar: "crew", accessory: null },
  { value: 3, label: "青色耳机", top: "#1E8A7A", accent: "#E3F2EF", collar: "v", accessory: "headphones" },
  { value: 4, label: "紫色眼镜", top: "#7C5CBF", accent: "#F0EAF9", collar: "v", accessory: "glasses" },
  { value: 5, label: "橙色圆领", top: "#D97B29", accent: "#FAF0E2", collar: "crew", accessory: null },
  { value: 6, label: "玫红衬衫", top: "#C6507B", accent: "#FAE9F1", collar: "v", accessory: null },
  { value: 7, label: "金色眼镜", top: "#C58A32", accent: "#F8F0E0", collar: "v", accessory: "glasses" },
  { value: 8, label: "灰色圆领", top: "#75756E", accent: "#EFEFEA", collar: "crew", accessory: null },
  { value: 9, label: "绿色耳机", top: "#238B61", accent: "#E3F2EC", collar: "v", accessory: "headphones" },
  { value: 10, label: "正装领带", top: "#242424", accent: "#C58A32", collar: "suit", accessory: "tie" },
];

export function outfitDef(outfit: number): OutfitDef {
  return OUTFITS.find((o) => o.value === outfit) ?? OUTFITS[0];
}

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) && n >= lo && n <= hi ? Math.round(n) : fallback;
}

/** 把任意来源的原始值规整成合法形象配置；不是对象或为空对象时返回
 * null，表示「未设置形象」，调用方应回退到旧 avatar 预设渲染。 */
export function normalizeAppearance(raw: unknown): AgentAppearance | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (Object.keys(raw as Record<string, unknown>).length === 0) return null;
  const r = raw as Record<string, unknown>;
  return {
    gender: r.gender === "male" ? "male" : "female",
    skin: clampInt(r.skin, 1, 5, DEFAULT_APPEARANCE.skin),
    outfit: clampInt(r.outfit, 1, 10, DEFAULT_APPEARANCE.outfit),
    hair: clampInt(r.hair, 1, 6, DEFAULT_APPEARANCE.hair),
  };
}
