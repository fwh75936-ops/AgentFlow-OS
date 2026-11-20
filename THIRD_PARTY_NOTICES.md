# 第三方组件与许可证声明

AgentFlow OS 的界面组件体系直接采用以下开源组件源码（均保留其原始许可证）：

## shadcn/ui（MIT License）

- 来源仓库：https://github.com/shadcn-ui/ui
- 取用版本：仓库提交 `84bd724d97005f98b54d064aed5883b79aac1fd3` 中 `apps/www/registry/new-york/ui/` 的官方组件源码
- 落位：`apps/desktop/src/components/ui/`
- 组件清单：button、card、badge、tabs、dialog、input、select、dropdown-menu、tooltip、progress、separator、avatar、table、scroll-area、skeleton、switch、textarea、label
- 许可证：MIT（Copyright (c) 2023 shadcn）
- 说明：主题变量已映射为本项目自有配色 AgentFlow Pure Light；组件源码按官方形态保留，仅做主题与语言适配。

## Kibo UI（MIT License）

- 来源仓库：https://github.com/shadcnblocks/kibo
- 取用文件：`packages/status/index.tsx`、`packages/avatar-stack/index.tsx`（main 分支）
- 落位：`apps/desktop/src/components/kibo/`
- 组件清单：status（Status / StatusIndicator / StatusLabel）、avatar-stack（AvatarStack）
- 许可证：MIT
- 说明：Status 的默认英文标签在产品界面中始终由调用方传入中文文案，不直接呈现。

## 运行时依赖（各自许可证）

- Tailwind CSS v3（MIT）与 tailwindcss-animate（MIT）：样式生成
- Radix UI 系列包 `@radix-ui/react-*`（MIT）：dialog、dropdown-menu、tabs、select、tooltip、avatar、progress、separator、scroll-area、switch、label、slot 的底层无障碍原语
- clsx（MIT）与 tailwind-merge（MIT）：`src/lib/utils.ts` 的 cn 工具
- class-variance-authority（Apache-2.0）：组件变体定义
- lucide-react（ISC）：界面图标

## 其余既有声明

- Motion（MIT）：动画引擎，用法见 `apps/desktop/src/motion/`
- React Flow / @xyflow/react（MIT）：工作流画布

本文件只记录直接 vendored 的组件源码与关键依赖；完整依赖清单以各包的 package.json 与 node_modules 中的许可证文件为准。

## Phaser 3（MIT License）

- 来源：https://github.com/phaserjs/phaser（npm 包 `phaser@3.90.0`）
- 用途：仅用于「AI 工作室」页面的 2D 像素办公室渲染（`apps/desktop/src/features/pixel-studio/`），按需动态加载，不接管 React 应用
- 许可证：MIT（Copyright (c) 2025 Richard Davey, Phaser Studio）
- 美术素材说明：工作室的全部像素素材（人物、家具、地图、气泡图标）均为本项目原创、运行时程序化绘制，未使用任何第三方游戏素材或参考项目的代码与资源。Pixel Agents / Claude Office / Pixel Office / SkyOffice 仅作产品形态参考，未复制其代码或素材。
