import streamlit as st
from datetime import datetime
import base64, time, re, requests, io, os, zipfile
from openai import OpenAI  # 统一使用 OpenAI SDK 调用 ProAI
from PIL import Image


# =========================================================
# 0. 安全逻辑锁
# =========================================================
def check_security_lock():
    deadline = datetime(2026, 6, 1, 17, 15)
    if datetime.now() >= deadline:
        st.error("🔒 系统维护中，请联系管理员。")
        st.stop()


check_security_lock()

# =========================================================
# 1. 基础配置与环境加载
# =========================================================
st.set_page_config(page_title="贝歌流水线 ProAI 版", layout="wide")


def load_c(fn, d):
    try:
        p = os.path.join(os.getcwd(), fn)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                return f.read().strip()
    except:
        pass
    return d


if 'pool' not in st.session_state: st.session_state.pool = {}
if 'wm_bytes' not in st.session_state: st.session_state.wm_bytes = None
if 'is_running' not in st.session_state: st.session_state.is_running = False


# =========================================================
# 2. 核心工具函数 (同步 main_proai 逻辑)
# =========================================================
def get_auth():
    try:
        # 建议在 Streamlit Secrets 中配置
        k_key = st.secrets.get("KIMI_API_KEY", "你的KIMI_KEY")
        p_key = st.secrets.get("PROAI_API_KEY", "你的PROAI_KEY")
        return k_key, p_key
    except Exception:
        st.error("❌ 未检测到 API Key。")
        st.stop()


def extract_url(text):
    """从 ProAI 返回的文本中提取图片 URL"""
    # 优先匹配 Markdown 格式 ![alt](url)
    markdown_match = re.search(r'!\[.*?\]\s*\((https?://[^\s\)]+)\)', text)
    if markdown_match: return markdown_match.group(1).strip()

    # 匹配括号中的 URL
    paren_match = re.search(r'\((https?://[^\s\)]+)\)', text)
    if paren_match: return paren_match.group(1).strip()

    # 兜底：直接匹配 URL
    direct_match = re.search(r'(https?://[^\s\u4e00-\u9fa5\)\!]+)', text)
    if direct_match: return direct_match.group(1).strip()
    return None


def api_vision(f_b64, prompt):
    k_key, _ = get_auth()
    client = OpenAI(api_key=k_key, base_url="https://api.moonshot.cn/v1")

    resp = client.chat.completions.create(
        model="moonshot-v1-8k-vision-preview",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f_b64}"}}
        ]}],
        temperature=0.3
    )
    return resp.choices[0].message.content.strip()


def api_image_proai(f_b64, prompt):
    """使用 gpt-image-2 模型生图"""
    _, p_key = get_auth()
    # 这里的 base_url 参考 main_proai 中的配置
    client = OpenAI(api_key=p_key, base_url="https://proaiapi.tech/v1")

    resp = client.chat.completions.create(
        model="gpt-image-2",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f_b64}"}}
            ]
        }],
        # 对应 main_proai 中的 extra_body
        extra_body={"response_format": {"type": "image_url"}}
    )

    raw_content = resp.choices[0].message.content.strip()
    img_url = extract_url(raw_content)

    if img_url:
        img_res = requests.get(img_url, timeout=60)
        img_res.raise_for_status()
        return img_res.content
    else:
        raise Exception(f"无法从响应中解析 URL: {raw_content[:100]}...")


def apply_wm(img_bytes, wm_bytes):
    base = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    logo = Image.open(io.BytesIO(wm_bytes)).convert("RGBA")
    scale = (base.size[0] * 0.15) / logo.size[0]
    logo = logo.resize((int(logo.size[0] * scale), int(logo.size[1] * scale)), Image.Resampling.LANCZOS)
    tmp = Image.new("RGBA", base.size, (0, 0, 0, 0))
    tmp.paste(base, (0, 0))
    r, g, b, a = logo.split()
    logo.putalpha(a.point(lambda i: int(i * 0.5)))
    tmp.paste(logo, (base.size[0] - logo.size[0] - 20, 20), mask=logo)
    buf = io.BytesIO()
    tmp.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def split_blocks(txt: str) -> list[str]:
    # 增强版切分逻辑，同步 main_proai
    if "###" in txt:
        parts = re.split(r'###\s*\d+\.', txt)
    else:
        pattern = r'(?m)^(?:(?:\d+[\.、\s]+)|(?:场景\s*\d+[:：\s]*)|(?:描述词\s*\d+[:：\s]*)|(?:[-*]\s+))'
        parts = re.split(pattern, txt)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


# =========================================================
# 3. 核心执行逻辑
# =========================================================
def run_pipeline(mode):
    st.session_state.is_running = True
    for fid, info in st.session_state.pool.items():
        if not st.session_state.is_running: break
        try:
            # 1. 标题
            if (mode in ['title', 'all']) and not info["title"]:
                info["status"] = "正在生成标题..."
                info["title"] = api_vision(info["b64"], st.session_state.t_p_val)
                st.rerun()

            # 2. 脚本
            if (mode in ['script', 'all']) and info["title"] and not info["tasks"]:
                info["status"] = "正在拆解描述词..."
                raw_txt = api_vision(info["b64"], f"参考标题：{info['title']}\n\n任务指令：{st.session_state.s_p_val}")
                blocks = split_blocks(raw_txt)
                info["tasks"] = [{"prompt": b, "img": None, "is_wm": False} for b in blocks]
                st.rerun()

            # 3. 绘图 (切换为 ProAI 引擎)
            if (mode in ['image', 'all']) and info["tasks"]:
                for i, t in enumerate(info["tasks"]):
                    if not st.session_state.is_running: break
                    if not t["img"]:
                        info["status"] = f"正在绘图 {i + 1}/{len(info['tasks'])}..."
                        cur_p = st.session_state.get(f"pa_{fid}_{i}", t["prompt"])
                        t["img"] = api_image_proai(info["b64"], cur_p)
                        st.rerun()
                info["status"] = "✅ 已完成"
        except Exception as e:
            info["status"] = f"❌ 失败: {str(e)}"
            st.session_state.is_running = False
            return
    st.session_state.is_running = False
    st.rerun()


# =========================================================
# 4. UI 布局
# =========================================================
with st.sidebar:
    st.header("⚙️ ProAI 控制面板")
    st.info("模型已切换为: gpt-image-2")

    if st.session_state.is_running:
        if st.button("🛑 停止执行", type="primary", use_container_width=True):
            st.session_state.is_running = False
            st.rerun()

    st.divider()
    wm_f = st.file_uploader("🖼️ 上传水印 (PNG)", type=["png"])
    if wm_f: st.session_state.wm_bytes = wm_f.getvalue()

    if st.button("🌊 批量添加水印", use_container_width=True):
        if st.session_state.wm_bytes:
            for info in st.session_state.pool.values():
                for t in info["tasks"]:
                    if t["img"] and not t.get("is_wm"):
                        t["img"] = apply_wm(t["img"], st.session_state.wm_bytes)
                        t["is_wm"] = True
            st.success("水印处理完毕！")
            st.rerun()

    if st.button("🗑️ 清空任务池", use_container_width=True):
        st.session_state.pool = {}
        st.rerun()

st.title("贝歌流水线 vProAI 🚀")

col_p1, col_p2 = st.columns(2)
with col_p1:
    st.session_state.t_p_val = st.text_area("✍️ 标题 Prompt", value=load_c("prompt_title.txt", "生成标题"), height=200)
with col_p2:
    st.session_state.s_p_val = st.text_area("📜 脚本 Prompt", value=load_c("prompt_photo.txt", "拆解脚本"), height=200)

st.divider()
btns = st.columns([1, 1, 1, 1, 1.5])
if btns[0].button("🚀 全量执行", type="primary", use_container_width=True): run_pipeline('all')
if btns[1].button("✍️ 批量标题", use_container_width=True): run_pipeline('title')
if btns[2].button("📜 批量脚本", use_container_width=True): run_pipeline('script')
if btns[3].button("🎨 批量图片", use_container_width=True): run_pipeline('image')

if st.session_state.is_running:
    for fid, info in st.session_state.pool.items():
        if "正在" in info["status"]:
            st.info(f"🚀 正在处理: {info['name']} | {info['status']}")

up_files = st.file_uploader("📸 批量上传素材", accept_multiple_files=True)
if up_files:
    for f in up_files:
        fid = f"{f.name}_{f.size}"
        if fid not in st.session_state.pool:
            st.session_state.pool[fid] = {
                "name": f.name, "b64": base64.b64encode(f.getvalue()).decode(),
                "raw": f.getvalue(), "title": "", "tasks": [], "status": "⏳ 待命"
            }

if st.session_state.pool:
    for fid, info in st.session_state.pool.items():
        with st.container(border=True):
            cl, cr = st.columns([1, 4])
            with cl:
                st.image(info["raw"], caption=info["name"])
                if st.button("🔄 重置", key=f"rs_{fid}", use_container_width=True):
                    info.update({"title": "", "tasks": [], "status": "⏳ 待命"})
                    st.rerun()
            with cr:
                st.markdown(f"状态: :green[`{info['status']}`]")
                ti_val = st.text_input("标题", value=info["title"], key=f"ti_{fid}")
                info["title"] = ti_val

                if info["tasks"]:
                    st.write("---")
                    sub_cols = st.columns(len(info["tasks"]))
                    for i, t in enumerate(info["tasks"]):
                        with sub_cols[i]:
                            if t["img"]:
                                st.image(t["img"], use_container_width=True)
                                st.download_button("📥", t["img"], f"{fid}_{i}.jpg", key=f"dl_{fid}_{i}")

                            t["prompt"] = st.text_area(f"描述词 {i + 1}", value=t["prompt"], key=f"pa_{fid}_{i}",
                                                       height=100)

                            if st.button(f"🎨 重绘", key=f"re_{fid}_{i}", use_container_width=True):
                                t["img"] = api_image_proai(info["b64"], t["prompt"])
                                t["is_wm"] = False
                                st.rerun()

    # 打包下载
    all_imgs = []
    for info in st.session_state.pool.values():
        for i, t in enumerate(info["tasks"]):
            if t["img"]: all_imgs.append((f"{info['name']}_{i + 1}.jpg", t["img"]))

    if all_imgs:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for fn, data in all_imgs: z.writestr(fn, data)
        btns[4].download_button(f"📦 下载 ({len(all_imgs)})", buf.getvalue(), "images.zip", type="primary",
                                use_container_width=True)

if st.session_state.is_running:
    time.sleep(0.5)
    st.rerun()