# 员工形象配置契约

一份配置，两个渲染器：员工的形象只存一份配置，桌面端的平面头像和像素 AI 工作室里的人物精灵都按这份配置渲染，保证同一个人到哪都是同一个样子。

- 存储：后端 `agents` 表的 `appearance` JSON 列
- 前端契约源头：`apps/desktop/src/lib/appearance.ts`，类型与全部取值表都在这一个文件里，像素工作室直接 import，不要另抄一份
- 平面头像渲染：`apps/desktop/src/components/AgentAvatar.tsx` 的 `AppearanceFigure`
- 本文档的字段名与取值范围一经定版不要更改；要扩展先改 `appearance.ts` 并同步本文档

## 字段结构

```json
{ "gender": "female", "skin": 2, "outfit": 1, "hair": 1 }
```

| 字段 | 类型 | 取值 | 含义 |
| --- | --- | --- | --- |
| gender | 字符串 | female 或 male | 形象性别，决定体型轮廓与发型表 |
| skin | 整数 | 1 到 5 | 肤色档位，色值见下表 |
| outfit | 整数 | 1 到 10 | 装扮编号，定义见下表 |
| hair | 整数 | 1 到 6 | 发型编号，含义按 gender 区分，见下表 |

四个字段都是必填语义：后端保存时逐字段规整，越界或缺失的字段回落到默认值（gender 回落 female、skin 回落 2、outfit 回落 1、hair 回落 1），不整单拒绝。

空对象或字段缺失表示这位员工还没有设置形象，属于未配置状态：界面回退到旧的 `avatar` 预设 ID 渲染（预设表在 `AgentAvatar.tsx` 的 `AVATAR_PRESETS`），再没有就显示姓名首字色块。只要 `appearance` 是非空对象，一律以它为准渲染，`avatar` 字段保留不动作历史兼容。

发色不单独存字段，由发型编号固定决定，见发型表。这样只凭上面四个字段就能确定性地渲染出完整形象，两个渲染器不会画得不一样。

## 肤色表

| skin | 名称 | 色值 |
| --- | --- | --- |
| 1 | 浅白 | #F8DCBE |
| 2 | 自然 | #F3CFA4 |
| 3 | 小麦 | #DFA96F |
| 4 | 深麦 | #B07B4F |
| 5 | 深棕 | #8D5A33 |

阴影不需要另存色值：平面头像的颈部阴影是主色叠 8% 黑，像素精灵按同一口径取暗一档即可。

## 发型表

女性（gender 为 female）：

| hair | 名称 | 款式大类 | 发色 |
| --- | --- | --- | --- |
| 1 | 长发 | long | #2E2A26 |
| 2 | 波波头 | bob | #5A3A22 |
| 3 | 丸子头 | bun | #2E2A26 |
| 4 | 马尾 | ponytail | #3A2A1E |
| 5 | 波浪长发 | long | #D9A441 |
| 6 | 短发 | short | #7A3B1E |

男性（gender 为 male）：

| hair | 名称 | 款式大类 | 发色 |
| --- | --- | --- | --- |
| 1 | 短发 | short | #23201D |
| 2 | 分头 | sidepart | #4A3226 |
| 3 | 寸头 | buzz | #1F1B18 |
| 4 | 卷发 | curly | #3A2417 |
| 5 | 银灰短发 | short | #B9BDC4 |
| 6 | 正装分头 | sidepart | #35322E |

款式大类就是 `appearance.ts` 里 `HairDef.style` 的值，像素工作室按大类选发型底图，再按发色调色。

## 装扮表

衣色全部取自已定版的分类色板，正装一款用品牌墨色。不引入新色系。

| outfit | 名称 | 上衣主色 | 搭配色 | 领型 | 配饰 |
| --- | --- | --- | --- | --- | --- |
| 1 | 蓝色衬衫 | #3978C6 | #E7EFF9 | V 领 | 无 |
| 2 | 绿色圆领 | #238B61 | #E3F2EC | 圆领 | 无 |
| 3 | 青色耳机 | #1E8A7A | #E3F2EF | V 领 | 耳机 |
| 4 | 紫色眼镜 | #7C5CBF | #F0EAF9 | V 领 | 眼镜 |
| 5 | 橙色圆领 | #D97B29 | #FAF0E2 | 圆领 | 无 |
| 6 | 玫红衬衫 | #C6507B | #FAE9F1 | V 领 | 无 |
| 7 | 金色眼镜 | #C58A32 | #F8F0E0 | V 领 | 眼镜 |
| 8 | 灰色圆领 | #75756E | #EFEFEA | 圆领 | 无 |
| 9 | 绿色耳机 | #238B61 | #E3F2EC | V 领 | 耳机 |
| 10 | 正装领带 | #242424 | #C58A32 | 正装 | 领带 |

搭配色的用法：圆领款画在领口带上，正装款是领带颜色，其余款式在平面头像里暂未直接用到，像素工作室可用作服装的第二配色。

## 现有 4 名员工的配置

| 员工 | gender | skin | outfit | hair | 对应旧预设 |
| --- | --- | --- | --- | --- | --- |
| 林晓 · 产品经理 | female | 1 | 1 | 1 | avatar-01 长发蓝衫 |
| 陈工 · 程序员 | male | 2 | 3 | 1 | avatar-05 短发耳机青衫 |
| 苏晴 · 测试工程师 | female | 2 | 4 | 2 | avatar-02 波波头紫衫眼镜 |
| AI总管 | male | 2 | 10 | 2 | avatar-13 正装领带墨色 |

四套配置互不相同，且与上一轮固定预设的观感一一对应，切换到新系统后人物长相不跳变。

## 像素工作室消费方式

工作室从员工接口拿到 `appearance` 后（`GET /api/agents` 与详情接口都原样带出），按下面四步选素材，不要自己再存一份形象数据：

1. 按 gender 选体型与发型表：female 用女性轮廓与 `FEMALE_HAIRS`，male 用男性轮廓与 `MALE_HAIRS`。
2. 按 hair 在对应性别的发型表里取款式大类与发色，选发型底图并按发色调色。
3. 按 skin 在肤色表里取主色，作为精灵的肤色调色板基准。
4. 按 outfit 在装扮表里取上衣主色与搭配色，给服装上色；领型与配饰决定服装款式图层（圆领/V 领/正装三款式，眼镜、耳机、领带三件配饰按需叠加）。

`appearance` 为空对象时按未配置处理：可回退用旧预设 ID 对应的观感，或给一个中性默认形象，但不要报错、不要自己编一套配置写回。

类型与常量直接从 `apps/desktop/src/lib/appearance.ts` import：`AgentAppearance`、`SKIN_TONES`、`FEMALE_HAIRS`、`MALE_HAIRS`、`OUTFITS`，以及 `normalizeAppearance`（规整任意来源的原始值，非法时返回 null）和 `skinBase`、`hairDef`、`outfitDef` 三个查表函数。

## 兼容说明

- `avatar` 字段与 14 款旧预设保留不动：老员工、老备份恢复出来的数据没有 `appearance` 时照常显示旧头像。
- 员工复制、备份导出与恢复都走整行数据，`appearance` 自动跟随，不需要额外处理。
- 后端规整逻辑在 `app/schemas.py` 的 `normalize_appearance`，与前端 `appearance.ts` 的 `normalizeAppearance` 口径一致：空 dict 是未配置，其余逐字段回落默认值。
