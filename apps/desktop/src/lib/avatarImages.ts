/* 生成头像图集的查表模块：按形象配置的 {gender, skin, outfit} 三维
 * 查到一张像素风头像图（src/assets/avatars/{f|m}-s{skin}-o{outfit}.png，
 * 共 2 性别 × 5 肤色 × 10 装扮 = 100 张，生成与自查记录见该目录
 * README.md）。发型不在查表维度内：每列（同 gender + outfit）图集
 * 统一采用一个契约内发型，编号记录在 README 的映射表里。
 *
 * 查不到图（配置越界或图集缺失）时返回 null，调用方必须回退到
 * AppearanceFigure 的 SVG 渲染，回退链不能断。 */

import type { AgentAppearance } from "./appearance";

const modules = import.meta.glob<string>("../assets/avatars/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

const BY_KEY = new Map<string, string>();
for (const [path, url] of Object.entries(modules)) {
  const m = /([fm])-s(\d+)-o(\d+)\.png$/.exec(path);
  if (m) BY_KEY.set(`${m[1]}-${m[2]}-${m[3]}`, url);
}

/** 图集实际载入的张数（正常应为 100），供自查/调试使用。 */
export const AVATAR_IMAGE_COUNT = BY_KEY.size;

export function avatarImageFor(appearance: AgentAppearance): string | null {
  const g = appearance.gender === "male" ? "m" : "f";
  return BY_KEY.get(`${g}-${appearance.skin}-${appearance.outfit}`) ?? null;
}
