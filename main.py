import streamlit as st
from datetime import datetime
import base64, time, re, requests, io, os, zipfile
from volcenginesdkarkruntime import Ark
from PIL import Image

# 隐藏右下角的 "Made with Streamlit" 和右上角的菜单
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# =========================================================
# 0. 安全逻辑锁：时间到达 2026-03-01 17:15 后白屏
# =========================================================
def check_security_lock():
    # 设定截止时间
    deadline = datetime(2026, 7, 11, 17, 15)
    if datetime.now() >= deadline:
        st.stop()


check_security_lock()

# =========================================================
# 1. 基础配置与环境加载
# =========================================================
st.set_page_config(page_title="贝歌流水线 v40.5", layout="wide")


def load_c(fn, d):
    try:
        # 优化云端路径读取逻辑
        p = os.path.join(os.getcwd(), fn)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                return f.read().strip()
    except:
        pass
    return d


# 初始化 session_state
if 'pool' not in st.session_state: st.session_state.pool = {}
if 'wm_bytes' not in st.session_state: st.session_state.wm_bytes = None
if 'is_running' not in st.session_state: st.session_state.is_running = False
if 'run_mode' not in st.session_state: st.session_state.run_mode = None


# =========================================================
# 2. API 引擎与逻辑工具
# =========================================================
def get_auth():
    """
    从 Streamlit Secrets 安全获取 Key。
    请在 Streamlit Cloud 后台 Advanced Settings 填写：
    KIMI_API_KEY = "your_sk_key"
    ARK_API_KEY = "your_fb_key"
    """
    try:
        k_key = st.secrets["KIMI_API_KEY"]
        a_key = st.secrets["ARK_API_KEY"]
        return k_key, a_key
    except Exception:
        st.error("❌ 未在 Secrets 中检测到 API Key，请检查 Advanced Settings。")
        st.stop()


def api_vision(f_b64, prompt):
    k, _ = get_auth()
    url = "https://api.moonshot.cn/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {k}"}
    payload = {
        "model": "moonshot-v1-8k-vision-preview",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f_b64}"}}
        ]}], "temperature": 0.3
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    res_json = resp.json()

    # 彻底解决 'choices' 报错的防护逻辑
    if 'choices' in res_json:
        return res_json['choices'][0]['message']['content'].strip()
    else:
        error_msg = res_json.get('error', {}).get('message', '未知接口错误')
        raise Exception(f"Kimi API 报错: {error_msg}")


def api_image(f_b64, prompt, size):
    _, a = get_auth()
    client = Ark(api_key=a, base_url="https://ark.cn-beijing.volces.com/api/v3")
    final_size = "2048x2048" if "2048" in size else size
    resp = client.images.generate(
        model="doubao-seedream-4-5-251128",
        prompt=prompt,
        image=f"data:image/jpeg;base64,{f_b64}",
        size=final_size,
        watermark=False
    )
    return requests.get(resp.data[0].url).content


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
    txt = re.sub(r"```|'''", '', txt)
    pattern = r'(?m)^(?:(?:\d+[\.、\s]+)|(?:场景\s*\d+[:：\s]*)|(?:描述词\s*\d+[:：\s]*)|(?:[-*]\s+))'
    parts = re.split(pattern, txt)
    return [p.strip() for p in parts if p.strip()]


# =========================================================
# 3. 核心执行逻辑
# =========================================================
def run_pipeline(mode):
    st.session_state.is_running = True
    st.session_state.run_mode = mode

    for fid, info in st.session_state.pool.items():
        if not st.session_state.is_running: break
        try:
            if (mode in ['title', 'all']) and not info["title"]:
                info["status"] = "正在生成标题..."
                info["title"] = api_vision_safe(info["b64"], st.session_state.t_p_val)
                st.rerun()

            if (mode in ['script', 'all']) and info["title"] and not info["tasks"]:
                info["status"] = "正在拆解视觉描述词..."
                raw_txt = api_vision_safe(info["b64"], st.session_state.s_p_val)
                blocks = split_blocks(raw_txt)
                info["tasks"] = [{"prompt": b, "img": None, "is_wm": False} for b in blocks]
                st.rerun()

            if (mode in ['image', 'all']) and info["tasks"]:
                for i, t in enumerate(info["tasks"]):
                    if not st.session_state.is_running: break
                    if not t["img"]:
                        info["status"] = f"正在绘图场景 {i + 1}/{len(info['tasks'])}..."
                        cur_p = st.session_state.get(f"pa_{fid}_{i}", t["prompt"])
                        t["img"] = api_image(info["b64"], cur_p, st.session_state.sz_val)
                        st.rerun()
                info["status"] = "✅ 已完成"
        except Exception as e:
            info["status"] = f"❌ 失败: {str(e)}"
            st.session_state.is_running = False
            return
    st.session_state.is_running = False
    st.rerun()


# =========================================================
# 4. UI 布局 (侧边栏)
# =========================================================
with st.sidebar:
    st.header("⚙️ 控制面板")
    st.session_state.sz_val = st.selectbox("出图尺寸", ["2048x2048", "1440x2560"])

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

# =========================================================
# 5. 主界面渲染
# =========================================================
st.title("贝歌流水线 v40.5 🚀")

col_p1, col_p2 = st.columns(2)
with col_p1:
    st.session_state.t_p_val = st.text_area("✍️ 标题 Prompt", value=load_c("prompt_title.txt", "生成标题"), height=400)
with col_p2:
    st.session_state.s_p_val = st.text_area("📜 脚本 Prompt", value=load_c("prompt_photo.txt", "拆解脚本"), height=400)

st.divider()
btns = st.columns([1, 1, 1, 1, 1.5])
if btns[0].button("🚀 全量执行", type="primary", use_container_width=True): run_pipeline('all')
if btns[1].button("✍️ 批量标题", use_container_width=True): run_pipeline('title')
if btns[2].button("📜 批量脚本", use_container_width=True): run_pipeline('script')
if btns[3].button("🎨 批量图片", use_container_width=True): run_pipeline('image')

if st.session_state.is_running:
    for fid, info in st.session_state.pool.items():
        if "正在" in info["status"]:
            st.info(f"🚀 当前正在处理: {info['name']} | {info['status']}")

up_files = st.file_uploader("📸 批量上传素材 (支持多选)", accept_multiple_files=True)
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
                if st.button("🔄 重置素材", key=f"rs_{fid}", use_container_width=True):
                    if f"ti_{fid}" in st.session_state: del st.session_state[f"ti_{fid}"]
                    info.update({"title": "", "tasks": [], "status": "⏳ 待命"})
                    st.rerun()
            with cr:
                st.markdown(f"卡片状态: :green[`{info['status']}`]")

                key_ti = f"ti_{fid}"
                if info["title"] and key_ti not in st.session_state:
                    st.session_state[key_ti] = info["title"]

                st.text_input("生成标题 (可微调)", key=key_ti)
                if key_ti in st.session_state: info["title"] = st.session_state[key_ti]

                if info["tasks"]:
                    st.write("---")
                    sub_cols = st.columns(len(info["tasks"]))
                    for i, t in enumerate(info["tasks"]):
                        with sub_cols[i]:
                            if t["img"]:
                                st.image(t["img"], use_container_width=True)
                                st.download_button("📥", t["img"], f"sc_{i}.jpg", key=f"dl_{fid}_{i}")

                            key_pa = f"pa_{fid}_{i}"
                            if key_pa not in st.session_state:
                                st.session_state[key_pa] = t["prompt"]

                            st.text_area(f"描述词 {i + 1}", key=key_pa, height=100)
                            if key_pa in st.session_state: t["prompt"] = st.session_state[key_pa]

                            if st.button(f"🎨 重绘", key=f"re_{fid}_{i}", use_container_width=True):
                                t["img"] = api_image(info["b64"], st.session_state[key_pa], st.session_state.sz_val)
                                t["is_wm"] = False
                                st.rerun()

    # 一键打包逻辑
    all_imgs = []
    for fid, info in st.session_state.pool.items():
        for i, t in enumerate(info["tasks"]):
            if t["img"]: all_imgs.append((f"{info['name']}_sc{i + 1}.jpg", t["img"]))

    if all_imgs:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for fn, data in all_imgs: z.writestr(fn, data)
        btns[4].download_button(f"📦 下载所有图片 ({len(all_imgs)})", buf.getvalue(), "all.zip", type="primary",
                                use_container_width=True)

# 自动刷新逻辑
if st.session_state.is_running:
    time.sleep(0.5)
    st.rerun()