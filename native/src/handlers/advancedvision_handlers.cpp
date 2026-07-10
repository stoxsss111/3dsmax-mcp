#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <ObjectWrapper.h>
#include <GraphicsWindow.h>
#include <mnmesh.h>

#include <algorithm>
#include <sstream>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

struct AdvancedVisionConfig {
    bool active = false;
    bool vertices = true;
    bool edges = true;
    bool faces = false;
    bool hud = false;
    int maxIds = 200;
    int idBase = 1;
    float textSize = 9.0f;
    std::string target;
};

static AdvancedVisionConfig g_cfg;
static bool g_registered = false;

static INode* ResolveTarget(Interface* ip) {
    if (!ip) return nullptr;
    if (!g_cfg.target.empty()) {
        return FindNodeByName(g_cfg.target);
    }
    if (ip->GetSelNodeCount() > 0) {
        return ip->GetSelNode(0);
    }
    return nullptr;
}

static bool InitPolyWrapper(INode* node, TimeValue t, ObjectWrapper& wrapper) {
    if (!node) return false;
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) return false;
    return wrapper.Init(t, os, FALSE, ObjectWrapper::polyEnable, ObjectWrapper::polyObject) != FALSE &&
           wrapper.GetPolyMesh() != nullptr;
}

static bool IsDead(MNVert* v) {
    return !v || v->GetFlag(MN_DEAD);
}

static bool IsDead(MNEdge* e) {
    return !e || e->GetFlag(MN_DEAD);
}

static bool IsDead(MNFace* f) {
    return !f || f->GetFlag(MN_DEAD);
}

static bool DrawLabel(GraphicsWindow* gw, const Point3& world, const std::wstring& label, const Point3& color) {
    if (!gw) return false;
    IPoint3 screen;
    DWORD clip = gw->wTransPoint(&world, &screen);
    if (clip != 0) return false;
    gw->setColor(TEXT_COLOR, color);
    gw->wText(&screen, label.c_str());
    return true;
}

static std::wstring PrefixLabel(const wchar_t prefix, int index) {
    std::wstringstream ss;
    ss << prefix << (index + g_cfg.idBase);
    return ss.str();
}

static int CapFor(int value) {
    return value < 0 ? 0 : value;
}

class AdvancedVisionOverlay : public ViewportDisplayCallback {
public:
    void Display(TimeValue t, ViewExp* vpt, int flags) override {
        if (!g_cfg.active || !vpt) return;
        GraphicsWindow* gw = vpt->getGW();
        if (!gw) return;

        Interface* ip = GetCOREInterface();
        INode* node = ResolveTarget(ip);
        if (!node) {
            DrawHud(gw, L"AV: select a poly object");
            return;
        }

        ObjectWrapper wrapper;
        if (!InitPolyWrapper(node, t, wrapper)) {
            DrawHud(gw, L"AV: selected object has no MNMesh output");
            return;
        }

        MNMesh* mesh = wrapper.GetPolyMesh();
        if (!mesh) return;

        const float oldSize = gw->GetTextPointSize();
        if (g_cfg.textSize > 0.0f) gw->SetTextPointSize(g_cfg.textSize);
        gw->setTransform(Matrix3(1));

        const Matrix3 tm = node->GetNodeTM(t);
        if (g_cfg.vertices) DrawVertices(gw, mesh, tm);
        if (g_cfg.edges) DrawEdges(gw, mesh, tm);
        if (g_cfg.faces) DrawFaces(gw, mesh, tm);
        if (g_cfg.hud) {
            std::wstring name = Utf8ToWide(WideToUtf8(node->GetName()));
            DrawHud(gw, L"AV: " + name);
        }
        gw->SetTextPointSize(oldSize);
    }

    void GetViewportRect(TimeValue t, ViewExp* vpt, Rect* rect) override {
        if (!rect) return;
        GraphicsWindow* gw = vpt ? vpt->getGW() : nullptr;
        rect->left = 0;
        rect->top = 0;
        rect->right = gw ? gw->getWinSizeX() : 100000;
        rect->bottom = gw ? gw->getWinSizeY() : 100000;
    }

    BOOL Foreground() override { return TRUE; }

private:
    void DrawHud(GraphicsWindow* gw, const std::wstring& text) {
        if (!g_cfg.hud || !gw) return;
        IPoint3 pos(12, 12, 0);
        gw->setColor(TEXT_COLOR, Point3(0.75f, 1.0f, 0.25f));
        gw->wText(&pos, text.c_str());
    }

    void DrawVertices(GraphicsWindow* gw, MNMesh* mesh, const Matrix3& tm) {
        int drawn = 0;
        const int cap = CapFor(g_cfg.maxIds);
        for (int i = 0; i < mesh->VNum(); ++i) {
            if (cap > 0 && drawn >= cap) break;
            MNVert* v = mesh->V(i);
            if (IsDead(v)) continue;
            Point3 world = v->p * tm;
            if (DrawLabel(gw, world, PrefixLabel(L'v', i), Point3(1.0f, 0.92f, 0.15f))) {
                ++drawn;
            }
        }
    }

    void DrawEdges(GraphicsWindow* gw, MNMesh* mesh, const Matrix3& tm) {
        int drawn = 0;
        const int cap = CapFor(g_cfg.maxIds);
        for (int i = 0; i < mesh->ENum(); ++i) {
            if (cap > 0 && drawn >= cap) break;
            MNEdge* e = mesh->E(i);
            if (IsDead(e)) continue;
            if (e->v1 < 0 || e->v2 < 0 || e->v1 >= mesh->VNum() || e->v2 >= mesh->VNum()) continue;
            MNVert* v1 = mesh->V(e->v1);
            MNVert* v2 = mesh->V(e->v2);
            if (IsDead(v1) || IsDead(v2)) continue;
            Point3 world = ((v1->p + v2->p) * 0.5f) * tm;
            if (DrawLabel(gw, world, PrefixLabel(L'e', i), Point3(0.10f, 0.88f, 1.0f))) {
                ++drawn;
            }
        }
    }

    void DrawFaces(GraphicsWindow* gw, MNMesh* mesh, const Matrix3& tm) {
        int drawn = 0;
        const int cap = CapFor(g_cfg.maxIds);
        for (int i = 0; i < mesh->FNum(); ++i) {
            if (cap > 0 && drawn >= cap) break;
            MNFace* f = mesh->F(i);
            if (IsDead(f) || f->deg <= 0 || !f->vtx) continue;
            Point3 center(0, 0, 0);
            int used = 0;
            for (int k = 0; k < f->deg; ++k) {
                int vi = f->vtx[k];
                if (vi < 0 || vi >= mesh->VNum()) continue;
                MNVert* v = mesh->V(vi);
                if (IsDead(v)) continue;
                center += v->p;
                ++used;
            }
            if (used == 0) continue;
            center /= static_cast<float>(used);
            Point3 world = center * tm;
            if (DrawLabel(gw, world, PrefixLabel(L'f', i), Point3(1.0f, 0.45f, 0.15f))) {
                ++drawn;
            }
        }
    }
};

static AdvancedVisionOverlay g_overlay;

static int CountLiveVerts(MNMesh* mesh) {
    int count = 0;
    for (int i = 0; mesh && i < mesh->VNum(); ++i) {
        if (!IsDead(mesh->V(i))) ++count;
    }
    return count;
}

static int CountLiveEdges(MNMesh* mesh) {
    int count = 0;
    for (int i = 0; mesh && i < mesh->ENum(); ++i) {
        if (!IsDead(mesh->E(i))) ++count;
    }
    return count;
}

static int CountLiveFaces(MNMesh* mesh) {
    int count = 0;
    for (int i = 0; mesh && i < mesh->FNum(); ++i) {
        if (!IsDead(mesh->F(i))) ++count;
    }
    return count;
}

static json CurrentStatus() {
    Interface* ip = GetCOREInterface();
    TimeValue t = ip ? ip->GetTime() : 0;
    INode* node = ResolveTarget(ip);

    json result;
    result["active"] = g_cfg.active;
    result["registered"] = g_registered;
    result["target"] = node ? WideToUtf8(node->GetName()) : "";
    result["idBase"] = g_cfg.idBase;
    result["components"] = {
        {"vertices", g_cfg.vertices},
        {"edges", g_cfg.edges},
        {"faces", g_cfg.faces},
    };
    result["maxIds"] = g_cfg.maxIds;

    ObjectWrapper wrapper;
    if (!node || !InitPolyWrapper(node, t, wrapper)) {
        result["mesh"] = nullptr;
        return result;
    }

    MNMesh* mesh = wrapper.GetPolyMesh();
    result["mesh"] = {
        {"vertices", CountLiveVerts(mesh)},
        {"edges", CountLiveEdges(mesh)},
        {"faces", CountLiveFaces(mesh)},
    };
    return result;
}

static void RegisterOverlay(Interface* ip) {
    if (!ip || g_registered) return;
    ip->RegisterViewportDisplayCallback(FALSE, &g_overlay);
    g_registered = true;
}

static void UnregisterOverlay(Interface* ip) {
    if (!ip || !g_registered) return;
    ip->UnRegisterViewportDisplayCallback(FALSE, &g_overlay);
    g_registered = false;
}

static bool ContainsComponent(const json& components, const std::string& name) {
    if (components.type() != json::value_t::array) return false;
    for (const auto& item : components) {
        if (item.type() != json::value_t::string) continue;
        std::string value = item.get<std::string>();
        std::transform(value.begin(), value.end(), value.begin(), ::tolower);
        if (value == name) return true;
    }
    return false;
}

static void ApplyShowConfig(const json& p) {
    if (p.contains("components") && p["components"].type() == json::value_t::array) {
        const json& components = p["components"];
        g_cfg.vertices = ContainsComponent(components, "vertices") || ContainsComponent(components, "verts") || ContainsComponent(components, "v");
        g_cfg.edges = ContainsComponent(components, "edges") || ContainsComponent(components, "e");
        g_cfg.faces = ContainsComponent(components, "faces") || ContainsComponent(components, "polygons") || ContainsComponent(components, "polys") || ContainsComponent(components, "f");
    }
    if (!p.contains("components")) {
        g_cfg.vertices = p.value("vertices", g_cfg.vertices);
        g_cfg.edges = p.value("edges", g_cfg.edges);
        g_cfg.faces = p.value("faces", g_cfg.faces);
    }
    g_cfg.hud = p.value("hud", g_cfg.hud);
    g_cfg.maxIds = (std::max)(0, p.value("max_ids", g_cfg.maxIds));
    g_cfg.idBase = p.value("id_base", g_cfg.idBase) == 0 ? 0 : 1;
    g_cfg.textSize = (std::max)(4.0f, (std::min)(24.0f, p.value("text_size", g_cfg.textSize)));
    g_cfg.target = p.value("target", g_cfg.target);
}

} // namespace

std::string NativeHandlers::AdvancedVision(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object()) p = json::object();

        std::string action = p.value("action", "show");
        std::transform(action.begin(), action.end(), action.begin(), ::tolower);

        Interface* ip = GetCOREInterface();
        if (!ip) throw std::runtime_error("3ds Max interface unavailable");

        if (action == "show" || action == "on" || action == "enable") {
            ApplyShowConfig(p);
            g_cfg.active = true;
            RegisterOverlay(ip);
        } else if (action == "hide" || action == "off" || action == "disable") {
            g_cfg.active = false;
            UnregisterOverlay(ip);
        } else if (action == "toggle") {
            if (g_cfg.active) {
                g_cfg.active = false;
                UnregisterOverlay(ip);
            } else {
                ApplyShowConfig(p);
                g_cfg.active = true;
                RegisterOverlay(ip);
            }
        } else if (action != "status") {
            throw std::runtime_error("advancedvision action must be show, hide, toggle, or status");
        }

        ip->NotifyViewportDisplayCallbackChanged(FALSE, &g_overlay);
        ip->RedrawViews(ip->GetTime());
        return CurrentStatus().dump();
    });
}
