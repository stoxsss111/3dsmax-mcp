#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <control.h>
#include <istdplug.h>
#include <TrackFlags.h>
#include <units.h>

#include <algorithm>
#include <cmath>
#include <cctype>
#include <set>
#include <unordered_set>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

static std::string LowerCopy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

static std::string TrimHash(std::string value) {
    if (!value.empty() && value[0] == '#') value.erase(value.begin());
    return value;
}

static TimeValue ToTimeValue(double value, const std::string& unit) {
    std::string u = LowerCopy(unit);
    if (u == "tick" || u == "ticks") {
        return static_cast<TimeValue>(std::llround(value));
    }
    if (u == "second" || u == "seconds" || u == "sec" || u == "secs") {
        return SecToTicks(value);
    }
    return static_cast<TimeValue>(std::llround(value * GetTicksPerFrame()));
}

static bool JsonIsArray(const json& value) {
    return value.type() == json::value_t::array;
}

static bool JsonIsBoolean(const json& value) {
    return value.type() == json::value_t::boolean;
}

static bool JsonIsNumber(const json& value) {
    return value.type() == json::value_t::number_integer ||
           value.type() == json::value_t::number_unsigned ||
           value.type() == json::value_t::number_float;
}

static bool JsonIsObject(const json& value) {
    return value.type() == json::value_t::object;
}

static bool JsonIsString(const json& value) {
    return value.type() == json::value_t::string;
}

static double TimeValueToFrame(TimeValue t) {
    return static_cast<double>(t) / static_cast<double>(GetTicksPerFrame());
}

static Control* ControlFromAnimatable(Animatable* anim) {
    if (!anim) return nullptr;
    if (Control* ctrl = static_cast<Control*>(anim->GetInterface(I_CONTROL))) {
        return ctrl;
    }

    SClass_ID sid = anim->SuperClassID();
    if (sid == CTRL_FLOAT_CLASS_ID || sid == CTRL_POINT3_CLASS_ID ||
        sid == CTRL_POSITION_CLASS_ID || sid == CTRL_ROTATION_CLASS_ID ||
        sid == CTRL_SCALE_CLASS_ID || sid == CTRL_MATRIX3_CLASS_ID) {
        return static_cast<Control*>(anim);
    }

    if (anim->NumSubs() > 0) {
        return ControlFromAnimatable(anim->SubAnim(0));
    }
    return nullptr;
}

static void CollectNodesForRequest(const json& p, Interface* ip, std::vector<INode*>& out) {
    std::unordered_set<INode*> seen;
    auto push = [&](INode* node) {
        if (node && seen.insert(node).second) out.push_back(node);
    };

    auto names = p.value("names", std::vector<std::string>{});
    if (!names.empty()) {
        for (const auto& name : names) {
            push(FindNodeByName(name));
        }
        return;
    }

    std::string target = LowerCopy(p.value("target", "selection"));
    if (target == "all" || target == "scene") {
        std::vector<INode*> nodes;
        CollectNodes(ip->GetRootNode(), nodes);
        for (INode* node : nodes) push(node);
        return;
    }

    const int selCount = ip->GetSelNodeCount();
    for (int i = 0; i < selCount; ++i) {
        push(ip->GetSelNode(i));
    }
}

struct TrackRef {
    INode* node = nullptr;
    Control* ctrl = nullptr;
    std::string label;
};

struct KeyframeBudget {
    int maxTracks = 1000;
    int maxKeys = 50000;
    int maxResults = 50;
    bool includeSamples = false;
};

static KeyframeBudget BudgetFromRequest(const json& p) {
    KeyframeBudget b;
    if (p.contains("budget") && JsonIsObject(p["budget"])) {
        const json& bp = p["budget"];
        b.maxTracks = std::max(1, bp.value("max_tracks", b.maxTracks));
        b.maxKeys = std::max(0, bp.value("max_keys", b.maxKeys));
        b.maxResults = std::max(0, bp.value("max_results", b.maxResults));
        b.includeSamples = bp.value("include_samples", b.includeSamples);
    }
    b.maxTracks = std::max(1, p.value("max_tracks", b.maxTracks));
    b.maxKeys = std::max(0, p.value("max_keys", b.maxKeys));
    b.maxResults = std::max(0, p.value("max_results", b.maxResults));
    b.includeSamples = p.value("include_samples", b.includeSamples);
    return b;
}

static void AddTrack(std::vector<TrackRef>& out, std::unordered_set<Control*>& seen,
                     INode* node, Control* ctrl, const std::string& label) {
    if (!node || !ctrl) return;
    if (!seen.insert(ctrl).second) return;
    out.push_back({node, ctrl, label});
}

static bool TrackFilterEquals(const std::string& tracks, const char* token) {
    return LowerCopy(tracks) == token;
}

static void ParseTrackFilter(const std::string& tracksRaw, bool& wantsPos, bool& wantsRot, bool& wantsScale, bool& wantsTransformOnly) {
    const std::string tracks = LowerCopy(tracksRaw);
    const bool wantsAll = tracks.empty() || TrackFilterEquals(tracks, "all") ||
                          TrackFilterEquals(tracks, "transform") || TrackFilterEquals(tracks, "prs") ||
                          TrackFilterEquals(tracks, "tm");
    wantsTransformOnly = TrackFilterEquals(tracks, "transform") || TrackFilterEquals(tracks, "tm");
    wantsPos = wantsAll || TrackFilterEquals(tracks, "position") || TrackFilterEquals(tracks, "pos");
    wantsRot = wantsAll || TrackFilterEquals(tracks, "rotation") || TrackFilterEquals(tracks, "rot");
    wantsScale = wantsAll || TrackFilterEquals(tracks, "scale") || TrackFilterEquals(tracks, "scl");
}

static int NodeDepth(INode* node) {
    int depth = 0;
    while (node) {
        INode* parent = node->GetParentNode();
        if (!parent || parent->IsRootNode()) break;
        depth++;
        node = parent;
    }
    return depth;
}

static void SortNodesByHierarchy(std::vector<INode*>& nodes) {
    std::sort(nodes.begin(), nodes.end(), [](INode* a, INode* b) {
        const int da = NodeDepth(a);
        const int db = NodeDepth(b);
        if (da != db) return da < db;
        return WideToUtf8(a->GetName()) < WideToUtf8(b->GetName());
    });
}

static bool UsesHierarchyOrder(const json& p, const std::string& action) {
    if (action == "loop") return true;
    std::string order = LowerCopy(p.value("order", "flat"));
    return order == "hierarchy" || order == "parents_first" || order == "parent_first";
}

static void CollectTracksForNode(INode* node, const json& p, std::vector<TrackRef>& out) {
    if (!node) return;
    std::unordered_set<Control*> seen;

    auto paths = p.value("track_paths", std::vector<std::string>{});
    for (const auto& path : paths) {
        Animatable* anim = ResolveSubAnimPath(node, NormalizeSubAnimPath(path));
        AddTrack(out, seen, node, ControlFromAnimatable(anim), path);
    }

    const bool hasTrackFilter = p.contains("tracks") && JsonIsString(p["tracks"]);
    if (!hasTrackFilter && !paths.empty()) return;

    std::string tracks = hasTrackFilter ? p["tracks"].get<std::string>() : "all";
    Control* tm = node->GetTMController();
    if (!tm) return;

    bool wantsPos = false;
    bool wantsRot = false;
    bool wantsScale = false;
    bool wantsTransformOnly = false;
    ParseTrackFilter(tracks, wantsPos, wantsRot, wantsScale, wantsTransformOnly);

    if (wantsTransformOnly) {
        AddTrack(out, seen, node, tm, "transform");
        return;
    }
    if (wantsPos) AddTrack(out, seen, node, tm->GetPositionController(), "position");
    if (wantsRot) AddTrack(out, seen, node, tm->GetRotationController(), "rotation");
    if (wantsScale) AddTrack(out, seen, node, tm->GetScaleController(), "scale");
}

static std::vector<TimeValue> TimesForSet(const json& p, Interface* ip) {
    std::vector<TimeValue> times;
    std::string unit = p.value("time_unit", "frames");
    if (p.contains("times") && JsonIsArray(p["times"])) {
        for (const auto& item : p["times"]) {
            if (JsonIsNumber(item)) times.push_back(ToTimeValue(item.get<double>(), unit));
        }
    }
    if (p.contains("time") && JsonIsNumber(p["time"])) {
        times.push_back(ToTimeValue(p["time"].get<double>(), unit));
    }
    if (times.empty()) times.push_back(ip->GetTime());

    std::sort(times.begin(), times.end());
    times.erase(std::unique(times.begin(), times.end()), times.end());
    return times;
}

static std::vector<int> KeyIndicesForStyle(Control* ctrl, const json& p, Interface* ip) {
    std::vector<int> indices;
    IKeyControl* keyControl = GetKeyControlInterface(ctrl);
    if (!ctrl || !keyControl) return indices;

    const int keyCount = keyControl->GetNumKeys();
    if (keyCount <= 0) return indices;

    const bool hasTimeFilter = p.contains("time") || p.contains("times");
    if (!hasTimeFilter) {
        for (int i = 0; i < keyCount; ++i) indices.push_back(i);
        return indices;
    }

    for (TimeValue t : TimesForSet(p, ip)) {
        int idx = ctrl->GetKeyIndex(t);
        if (idx >= 0 && idx < keyCount) indices.push_back(idx);
    }
    std::sort(indices.begin(), indices.end());
    indices.erase(std::unique(indices.begin(), indices.end()), indices.end());
    return indices;
}

static std::string ControllerClass(Control* ctrl) {
    if (!ctrl) return "";
    MSTR name;
    ctrl->GetClassName(name, false);
    return WideToUtf8(name.data());
}

static bool ReadValue(Control* ctrl, TimeValue t, json& value) {
    if (!ctrl) return false;
    Interval valid = FOREVER;
    SClass_ID sid = ctrl->SuperClassID();

    if (sid == CTRL_FLOAT_CLASS_ID) {
        float v = 0.0f;
        ctrl->GetValue(t, &v, valid, CTRL_ABSOLUTE);
        value = v;
        return true;
    }
    if (sid == CTRL_POINT3_CLASS_ID || sid == CTRL_POSITION_CLASS_ID) {
        Point3 v(0, 0, 0);
        ctrl->GetValue(t, &v, valid, CTRL_ABSOLUTE);
        value = json::array({v.x, v.y, v.z});
        return true;
    }
    if (sid == CTRL_ROTATION_CLASS_ID) {
        Quat q;
        q.Identity();
        ctrl->GetValue(t, &q, valid, CTRL_ABSOLUTE);
        value = json::array({q.x, q.y, q.z, q.w});
        return true;
    }
    if (sid == CTRL_SCALE_CLASS_ID) {
        ScaleValue s;
        ctrl->GetValue(t, &s, valid, CTRL_ABSOLUTE);
        value = json::array({s.s.x, s.s.y, s.s.z});
        return true;
    }
    if (sid == CTRL_MATRIX3_CLASS_ID) {
        Matrix3 tm(TRUE);
        ctrl->GetValue(t, &tm, valid, CTRL_ABSOLUTE);
        Point3 pos = tm.GetTrans();
        value = json::array({pos.x, pos.y, pos.z});
        return true;
    }
    return false;
}

static int ComponentIndexFromLabel(const std::string& label) {
    std::string l = LowerCopy(label);
    if (l.find("#x") != std::string::npos || l.find("x position") != std::string::npos ||
        l.find("x rotation") != std::string::npos || l.find("x scale") != std::string::npos) return 0;
    if (l.find("#y") != std::string::npos || l.find("y position") != std::string::npos ||
        l.find("y rotation") != std::string::npos || l.find("y scale") != std::string::npos) return 1;
    if (l.find("#z") != std::string::npos || l.find("z position") != std::string::npos ||
        l.find("z rotation") != std::string::npos || l.find("z scale") != std::string::npos) return 2;
    if (l.find("#w") != std::string::npos || l.find("w rotation") != std::string::npos) return 3;
    return -1;
}

static bool JsonNumberAt(const json& value, int index, float& out) {
    if (JsonIsNumber(value)) {
        out = value.get<float>();
        return true;
    }
    if (JsonIsArray(value) && !value.empty()) {
        int safeIndex = index >= 0 ? index : 0;
        if (safeIndex >= 0 && safeIndex < static_cast<int>(value.size()) && JsonIsNumber(value[safeIndex])) {
            out = value[safeIndex].get<float>();
            return true;
        }
    }
    return false;
}

static bool JsonPoint3Value(const json& value, Point3& out) {
    if (!JsonIsArray(value) || value.size() < 3) return false;
    if (!JsonIsNumber(value[0]) || !JsonIsNumber(value[1]) || !JsonIsNumber(value[2])) return false;
    out = Point3(value[0].get<float>(), value[1].get<float>(), value[2].get<float>());
    return true;
}

static bool AddJsonDelta(const json& base, const json& delta, int componentIndex, json& out) {
    if (JsonIsNumber(base)) {
        float d = 0.0f;
        if (!JsonNumberAt(delta, componentIndex, d)) return false;
        out = base.get<float>() + d;
        return true;
    }
    if (JsonIsArray(base)) {
        out = base;
        const int count = static_cast<int>(base.size());
        for (int i = 0; i < count; ++i) {
            if (!JsonIsNumber(base[i])) continue;
            float d = 0.0f;
            if (JsonIsArray(delta)) {
                if (i >= static_cast<int>(delta.size()) || !JsonIsNumber(delta[i])) continue;
                d = delta[i].get<float>();
            } else if (JsonIsNumber(delta)) {
                d = delta.get<float>();
            } else {
                continue;
            }
            out[i] = base[i].get<float>() + d;
        }
        return true;
    }
    return false;
}

static bool HasValueRequest(const json& p) {
    return p.contains("value") || p.contains("move");
}

static bool TargetValueFor(Control* ctrl, TimeValue t, const json& p, int componentIndex, json& target) {
    if (p.contains("value")) {
        const json& value = p["value"];
        if (componentIndex >= 0 && JsonIsArray(value) && componentIndex < static_cast<int>(value.size())) {
            target = value[componentIndex];
        } else {
            target = value;
        }
        return true;
    }

    if (p.contains("move")) {
        json base;
        if (!ReadValue(ctrl, t, base)) return false;
        return AddJsonDelta(base, p["move"], componentIndex, target);
    }

    return false;
}

static bool LooksLikeAxisComposite(Control* ctrl) {
    std::string cls = LowerCopy(ControllerClass(ctrl));
    return cls.find("xyz") != std::string::npos || cls.find("euler") != std::string::npos;
}

static bool WriteJsonValue(Control* ctrl, TimeValue dstTime, const json& value,
                           std::unordered_set<Control*>& visited, int componentIndex = -1) {
    if (!ctrl) return false;
    if (!visited.insert(ctrl).second) return false;

    if (LooksLikeAxisComposite(ctrl) && JsonIsArray(value)) {
        bool wroteChild = false;
        const int childCount = std::min<int>(ctrl->NumSubs(), static_cast<int>(value.size()));
        for (int i = 0; i < childCount; ++i) {
            if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
                wroteChild = WriteJsonValue(child, dstTime, value[i], visited, i) || wroteChild;
            }
        }
        if (wroteChild) return true;
    }

    SClass_ID sid = ctrl->SuperClassID();
    if (sid == CTRL_FLOAT_CLASS_ID) {
        float v = 0.0f;
        if (!JsonNumberAt(value, componentIndex, v)) return false;
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &v, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_POINT3_CLASS_ID || sid == CTRL_POSITION_CLASS_ID) {
        Point3 v;
        if (!JsonPoint3Value(value, v)) return false;
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &v, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_ROTATION_CLASS_ID && JsonIsArray(value) && value.size() >= 4) {
        Quat q(value[0].get<float>(), value[1].get<float>(), value[2].get<float>(), value[3].get<float>());
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &q, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_SCALE_CLASS_ID) {
        Point3 p3;
        if (!JsonPoint3Value(value, p3)) return false;
        ScaleValue s(p3);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &s, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_MATRIX3_CLASS_ID) {
        Point3 p3;
        if (!JsonPoint3Value(value, p3)) return false;
        Interval valid = FOREVER;
        Matrix3 tm(TRUE);
        ctrl->GetValue(dstTime, &tm, valid, CTRL_ABSOLUTE);
        tm.SetTrans(p3);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &tm, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    return false;
}

static bool WriteRequestedValue(Control* ctrl, TimeValue t, const json& p, int componentIndex) {
    json target;
    if (!TargetValueFor(ctrl, t, p, componentIndex, target)) return false;
    std::unordered_set<Control*> visited;
    return WriteJsonValue(ctrl, t, target, visited, componentIndex);
}

static bool WriteSampledValue(Control* ctrl, TimeValue srcTime, TimeValue dstTime, std::unordered_set<Control*>& visited) {
    if (!ctrl) return false;
    if (!visited.insert(ctrl).second) return false;

    if (LooksLikeAxisComposite(ctrl)) {
        bool wroteChild = false;
        for (int i = 0; i < ctrl->NumSubs(); ++i) {
            if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
                wroteChild = WriteSampledValue(child, srcTime, dstTime, visited) || wroteChild;
            }
        }
        if (wroteChild) return true;
    }

    Interval valid = FOREVER;
    SClass_ID sid = ctrl->SuperClassID();

    if (sid == CTRL_FLOAT_CLASS_ID) {
        float v = 0.0f;
        ctrl->GetValue(srcTime, &v, valid, CTRL_ABSOLUTE);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &v, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_POINT3_CLASS_ID || sid == CTRL_POSITION_CLASS_ID) {
        Point3 v(0, 0, 0);
        ctrl->GetValue(srcTime, &v, valid, CTRL_ABSOLUTE);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &v, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_ROTATION_CLASS_ID) {
        Quat q;
        q.Identity();
        ctrl->GetValue(srcTime, &q, valid, CTRL_ABSOLUTE);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &q, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_SCALE_CLASS_ID) {
        ScaleValue s;
        ctrl->GetValue(srcTime, &s, valid, CTRL_ABSOLUTE);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &s, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    if (sid == CTRL_MATRIX3_CLASS_ID) {
        Matrix3 tm(TRUE);
        ctrl->GetValue(srcTime, &tm, valid, CTRL_ABSOLUTE);
        ctrl->AddNewKey(dstTime, ADDKEY_INTERP);
        ctrl->SetValue(dstTime, &tm, TRUE, CTRL_ABSOLUTE);
        return true;
    }
    return false;
}

static bool WriteSampledValue(Control* ctrl, TimeValue srcTime, TimeValue dstTime) {
    std::unordered_set<Control*> visited;
    return WriteSampledValue(ctrl, srcTime, dstTime, visited);
}

static bool AddCurrentValueKey(Control* ctrl, TimeValue t) {
    return WriteSampledValue(ctrl, t, t);
}

class AnimateGuard {
public:
    AnimateGuard() : wasAnimating_(Animating() != 0) {
        if (!wasAnimating_) AnimateOn();
    }
    ~AnimateGuard() {
        if (!wasAnimating_) AnimateOff();
    }
private:
    bool wasAnimating_;
};

static int TangentTypeFromName(std::string value) {
    value = LowerCopy(TrimHash(value));
    if (value == "smooth") return BEZKEY_SMOOTH;
    if (value == "linear" || value == "line") return BEZKEY_LINEAR;
    if (value == "step" || value == "constant") return BEZKEY_STEP;
    if (value == "fast") return BEZKEY_FAST;
    if (value == "slow") return BEZKEY_SLOW;
    if (value == "custom" || value == "user" || value == "auto") return BEZKEY_USER;
    if (value == "flat") return BEZKEY_FLAT;
    return -1;
}

static int OrtFromName(std::string value) {
    value = LowerCopy(TrimHash(value));
    if (value == "constant" || value == "hold") return ORT_CONSTANT;
    if (value == "cycle") return ORT_CYCLE;
    if (value == "loop") return ORT_LOOP;
    if (value == "pingpong" || value == "ping_pong" || value == "oscillate") return ORT_OSCILLATE;
    if (value == "linear") return ORT_LINEAR;
    if (value == "identity") return ORT_IDENTITY;
    if (value == "relativerepeat" || value == "relative_repeat" || value == "relative") return ORT_RELATIVE_REPEAT;
    return -1;
}

static std::string OrtName(int value) {
    switch (value) {
        case ORT_CONSTANT: return "constant";
        case ORT_CYCLE: return "cycle";
        case ORT_LOOP: return "loop";
        case ORT_OSCILLATE: return "pingPong";
        case ORT_LINEAR: return "linear";
        case ORT_IDENTITY: return "identity";
        case ORT_RELATIVE_REPEAT: return "relativeRepeat";
        default: return "unknown";
    }
}

static bool LooksLikeBezier(Control* ctrl) {
    return LowerCopy(ControllerClass(ctrl)).find("bezier") != std::string::npos;
}

static bool LooksLikeTCB(Control* ctrl) {
    return LowerCopy(ControllerClass(ctrl)).find("tcb") != std::string::npos;
}

static int StyleOneController(Control* ctrl, const json& p, Interface* ip, std::unordered_set<Control*>& visited) {
    if (!ctrl) return 0;
    if (!visited.insert(ctrl).second) return 0;

    int changed = 0;
    IKeyControl* keyControl = GetKeyControlInterface(ctrl);
    if (keyControl) {
        std::vector<int> indices = KeyIndicesForStyle(ctrl, p, ip);
        const int keySize = keyControl->GetKeySize();
        if (keySize > 0 && !indices.empty()) {
            std::string keyType = p.value("key_type", "");
            std::string inType = p.value("in_type", keyType);
            std::string outType = p.value("out_type", keyType);
            int inTan = inType.empty() ? -1 : TangentTypeFromName(inType);
            int outTan = outType.empty() ? -1 : TangentTypeFromName(outType);

            bool isBezier = LooksLikeBezier(ctrl);
            bool isTCB = LooksLikeTCB(ctrl);
            json tcb = p.contains("tcb") && JsonIsObject(p["tcb"]) ? p["tcb"] : json::object();

            for (int idx : indices) {
                std::vector<unsigned char> bytes(static_cast<size_t>(keySize), 0);
                IKey* key = reinterpret_cast<IKey*>(bytes.data());
                keyControl->GetKey(idx, key);

                bool touched = false;
                if (isBezier) {
                    if (inTan >= 0) {
                        SetInTanType(key->flags, inTan);
                        touched = true;
                    }
                    if (outTan >= 0) {
                        SetOutTanType(key->flags, outTan);
                        touched = true;
                    }
                }

                if (isTCB && !tcb.empty()) {
                    ITCBKey* tcbKey = reinterpret_cast<ITCBKey*>(bytes.data());
                    if (tcb.contains("tension")) { tcbKey->tens = tcb["tension"].get<float>(); touched = true; }
                    if (tcb.contains("continuity")) { tcbKey->cont = tcb["continuity"].get<float>(); touched = true; }
                    if (tcb.contains("bias")) { tcbKey->bias = tcb["bias"].get<float>(); touched = true; }
                    if (tcb.contains("easeTo")) { tcbKey->easeIn = tcb["easeTo"].get<float>(); touched = true; }
                    if (tcb.contains("easeFrom")) { tcbKey->easeOut = tcb["easeFrom"].get<float>(); touched = true; }
                }

                if (touched) {
                    keyControl->SetKey(idx, key);
                    changed++;
                }
            }

            if (changed > 0) keyControl->SortKeys();
        }
    }

    for (int i = 0; i < ctrl->NumSubs(); ++i) {
        if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
            changed += StyleOneController(child, p, ip, visited);
        }
    }
    return changed;
}

static int StyleOneController(Control* ctrl, const json& p, Interface* ip) {
    std::unordered_set<Control*> visited;
    return StyleOneController(ctrl, p, ip, visited);
}

static int ApplyOrtOneController(Control* ctrl, const json& p, std::unordered_set<Control*>& visited) {
    if (!ctrl) return 0;
    if (!visited.insert(ctrl).second) return 0;

    int changed = 0;

    std::string before = p.value("before", "");
    std::string after = p.value("after", "");
    if (p.contains("out_of_range") && JsonIsObject(p["out_of_range"])) {
        const json& ort = p["out_of_range"];
        before = ort.value("before", before);
        after = ort.value("after", after);
        if (ort.contains("enabled") && JsonIsBoolean(ort["enabled"])) {
            ctrl->EnableORTs(ort["enabled"].get<bool>() ? TRUE : FALSE);
            changed++;
        }
    }
    if (p.contains("ort_enabled") && JsonIsBoolean(p["ort_enabled"])) {
        ctrl->EnableORTs(p["ort_enabled"].get<bool>() ? TRUE : FALSE);
        changed++;
    }

    int beforeOrt = before.empty() ? -1 : OrtFromName(before);
    int afterOrt = after.empty() ? -1 : OrtFromName(after);
    if (beforeOrt >= 0) {
        ctrl->SetORT(beforeOrt, ORT_BEFORE);
        changed++;
    }
    if (afterOrt >= 0) {
        ctrl->SetORT(afterOrt, ORT_AFTER);
        changed++;
    }

    for (int i = 0; i < ctrl->NumSubs(); ++i) {
        if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
            changed += ApplyOrtOneController(child, p, visited);
        }
    }
    return changed;
}

static int ApplyOrtOneController(Control* ctrl, const json& p) {
    std::unordered_set<Control*> visited;
    return ApplyOrtOneController(ctrl, p, visited);
}

static bool HasStyleRequest(const json& p) {
    return p.contains("key_type") || p.contains("in_type") || p.contains("out_type") || p.contains("tcb");
}

static bool HasOrtRequest(const json& p) {
    return p.contains("before") || p.contains("after") || p.contains("ort_enabled") || p.contains("out_of_range");
}

static int CountStyleKeys(Control* ctrl, const json& p, Interface* ip, std::unordered_set<Control*>& visited) {
    if (!ctrl) return 0;
    if (!visited.insert(ctrl).second) return 0;

    int total = 0;
    if (IKeyControl* keyControl = GetKeyControlInterface(ctrl)) {
        if (p.contains("time") || p.contains("times")) {
            total += static_cast<int>(KeyIndicesForStyle(ctrl, p, ip).size());
        } else {
            total += std::max(0, keyControl->GetNumKeys());
        }
    }
    for (int i = 0; i < ctrl->NumSubs(); ++i) {
        if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
            total += CountStyleKeys(child, p, ip, visited);
        }
    }
    return total;
}

static int CountStyleKeys(Control* ctrl, const json& p, Interface* ip) {
    std::unordered_set<Control*> visited;
    return CountStyleKeys(ctrl, p, ip, visited);
}

static void CollectKeyTimes(Control* ctrl, std::set<TimeValue>& times, std::unordered_set<Control*>& visited) {
    if (!ctrl) return;
    if (!visited.insert(ctrl).second) return;

    const int keyCount = ctrl->NumKeys();
    for (int i = 0; i < keyCount; ++i) {
        times.insert(ctrl->GetKeyTime(i));
    }
    for (int i = 0; i < ctrl->NumSubs(); ++i) {
        if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
            CollectKeyTimes(child, times, visited);
        }
    }
}

static bool FirstLastTimes(Control* ctrl, TimeValue& first, TimeValue& last) {
    std::set<TimeValue> times;
    std::unordered_set<Control*> visited;
    CollectKeyTimes(ctrl, times, visited);
    if (times.empty()) return false;

    first = *times.begin();
    last = *times.rbegin();
    return true;
}

static int UniqueKeyTimeCount(Control* ctrl) {
    std::set<TimeValue> times;
    std::unordered_set<Control*> visited;
    CollectKeyTimes(ctrl, times, visited);
    return static_cast<int>(times.size());
}

static int CountStyleableControllers(Control* ctrl, std::unordered_set<Control*>& visited) {
    if (!ctrl) return 0;
    if (!visited.insert(ctrl).second) return 0;

    int count = GetKeyControlInterface(ctrl) ? 1 : 0;
    for (int i = 0; i < ctrl->NumSubs(); ++i) {
        if (Control* child = ControlFromAnimatable(ctrl->SubAnim(i))) {
            count += CountStyleableControllers(child, visited);
        }
    }
    return count;
}

static int CountStyleableControllers(Control* ctrl) {
    std::unordered_set<Control*> visited;
    return CountStyleableControllers(ctrl, visited);
}

static int CountLogicalStyleKeys(Control* ctrl, const json& p, Interface* ip) {
    std::set<TimeValue> times;
    std::unordered_set<Control*> visited;
    CollectKeyTimes(ctrl, times, visited);
    if (times.empty()) return 0;

    if (p.contains("time") || p.contains("times")) {
        int count = 0;
        for (TimeValue t : TimesForSet(p, ip)) {
            if (times.find(t) != times.end()) count++;
        }
        return count;
    }
    return static_cast<int>(times.size());
}

static int EstimateSetStyleControllerCandidates(Control* ctrl, const json& p, Interface* ip) {
    const int styleable = CountStyleableControllers(ctrl);
    if (styleable <= 0) return 0;
    return styleable * static_cast<int>(TimesForSet(p, ip).size());
}

static int EstimateSetLogicalStyleCandidates(Control* ctrl, const json& p, Interface* ip) {
    return CountStyleableControllers(ctrl) > 0 ? static_cast<int>(TimesForSet(p, ip).size()) : 0;
}

static json TrackSummary(const TrackRef& tr) {
    json entry;
    entry["object"] = tr.node ? WideToUtf8(tr.node->GetName()) : "";
    entry["track"] = tr.label;
    entry["controller"] = ControllerClass(tr.ctrl);
    entry["keys"] = tr.ctrl ? UniqueKeyTimeCount(tr.ctrl) : 0;
    if (tr.ctrl && entry["keys"].get<int>() > 0) {
        TimeValue first = 0;
        TimeValue last = 0;
        if (FirstLastTimes(tr.ctrl, first, last)) {
            entry["first"] = TimeValueToFrame(first);
            entry["last"] = TimeValueToFrame(last);
        }
    }
    if (tr.ctrl) {
        entry["ort"] = {
            {"before", OrtName(tr.ctrl->GetORT(ORT_BEFORE))},
            {"after", OrtName(tr.ctrl->GetORT(ORT_AFTER))}
        };
    }
    return entry;
}

static json LoopGapEntry(const TrackRef& tr, TimeValue src, TimeValue dst) {
    json entry;
    entry["object"] = tr.node ? WideToUtf8(tr.node->GetName()) : "";
    entry["track"] = tr.label;
    entry["from"] = TimeValueToFrame(src);
    entry["to"] = TimeValueToFrame(dst);
    json fromValue;
    json toValue;
    const bool hasFrom = tr.ctrl && ReadValue(tr.ctrl, src, fromValue);
    const bool hasTo = tr.ctrl && ReadValue(tr.ctrl, dst, toValue);
    if (hasFrom) entry["fromValue"] = fromValue;
    if (hasTo) entry["toValue"] = toValue;
    entry["matches"] = hasFrom && hasTo && fromValue == toValue;
    return entry;
}

static json BuildKeyframeResult(
    const std::string& action,
    const std::vector<INode*>& nodes,
    const std::vector<TrackRef>& tracks,
    const KeyframeBudget& budget,
    int keyed,
    int matched,
    int styled,
    int styledControllerKeys,
    int ranged,
    int rangedControllerEdits,
    int styleKeyCount,
    int styleControllerKeyCount,
    const json& samples,
    const json& loopGaps,
    bool readOnly) {
    json trackInfo = json::array();
    const int reportLimit = std::min<int>(budget.maxResults, static_cast<int>(tracks.size()));
    for (int i = 0; i < reportLimit; ++i) trackInfo.push_back(TrackSummary(tracks[i]));

    json result;
    result["action"] = action;
    result["readOnly"] = readOnly;
    result["nodes"] = nodes.size();
    result["tracks"] = tracks.size();
    result["keyed"] = keyed;
    result["matched"] = matched;
    result["styledKeys"] = styled;
    result["styledControllerKeys"] = styledControllerKeys;
    result["outOfRangeEdits"] = ranged;
    result["outOfRangeControllerEdits"] = rangedControllerEdits;
    result["styleKeyCandidates"] = styleKeyCount;
    result["styleControllerKeyCandidates"] = styleControllerKeyCount;
    if (!loopGaps.empty()) result["loopGaps"] = loopGaps;
    if (budget.includeSamples) result["samples"] = samples;
    else result["samplesOmitted"] = true;
    result["sampleCount"] = keyed + matched;
    if (budget.includeSamples && result["sampleCount"].get<int>() > static_cast<int>(samples.size())) {
        result["samplesTruncated"] = true;
    }
    result["trackInfo"] = trackInfo;
    result["truncated"] = static_cast<int>(tracks.size()) > reportLimit;
    if (static_cast<int>(tracks.size()) > reportLimit) {
        result["omittedTrackInfo"] = static_cast<int>(tracks.size()) - reportLimit;
    }
    result["budget"] = {
        {"maxTracks", budget.maxTracks},
        {"maxKeys", budget.maxKeys},
        {"maxResults", budget.maxResults},
        {"includeSamples", budget.includeSamples}
    };
    return result;
}

} // namespace

std::string NativeHandlers::KeyframeTracks(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded()) throw std::runtime_error("Invalid JSON payload");

        Interface* ip = GetCOREInterface();
        std::string action = LowerCopy(p.value("action", "set"));
        if (action == "add" || action == "key") action = "set";
        if (action == "match_first_last" || action == "match_endpoints") action = "match";
        if (action == "tangent" || action == "tangents" || action == "key_type") action = "style";
        if (action == "range" || action == "out_of_range") action = "ort";
        if (action == "inspect" || action == "query" || action == "summary") action = "list";
        if (action == "loop_close" || action == "close_loop" || action == "loop_match") action = "loop";

        std::vector<INode*> nodes;
        CollectNodesForRequest(p, ip, nodes);
        if (nodes.empty()) {
            throw std::runtime_error("No target nodes found. Pass names, target='all', or select objects in 3ds Max.");
        }

        KeyframeBudget budget = BudgetFromRequest(p);
        if (p.contains("value") && p.contains("move")) {
            throw std::runtime_error("Pass either value or move, not both.");
        }

        if (action == "list") {
            std::vector<TrackRef> tracks;
            for (INode* node : nodes) CollectTracksForNode(node, p, tracks);
            if (tracks.empty()) {
                throw std::runtime_error("No matching animation tracks found.");
            }

            json loopGaps = json::array();
            std::string unit = p.value("time_unit", "frames");
            const bool hasLoopWindow = p.contains("from_time") && JsonIsNumber(p["from_time"]) &&
                                       p.contains("to_time") && JsonIsNumber(p["to_time"]);
            TimeValue loopSrc = 0;
            TimeValue loopDst = 0;
            if (hasLoopWindow) {
                loopSrc = ToTimeValue(p["from_time"].get<double>(), unit);
                loopDst = ToTimeValue(p["to_time"].get<double>(), unit);
                for (const TrackRef& tr : tracks) {
                    if (!tr.ctrl || UniqueKeyTimeCount(tr.ctrl) <= 0) continue;
                    json gap = LoopGapEntry(tr, loopSrc, loopDst);
                    if (budget.includeSamples || !gap.value("matches", false)) {
                        loopGaps.push_back(gap);
                    }
                    if (static_cast<int>(loopGaps.size()) >= budget.maxResults) break;
                }
            }

            json samples = json::array();
            if (budget.includeSamples && (p.contains("time") || p.contains("times"))) {
                for (TimeValue t : TimesForSet(p, ip)) {
                    for (const TrackRef& tr : tracks) {
                        if (!tr.ctrl) continue;
                        json sample;
                        sample["object"] = WideToUtf8(tr.node->GetName());
                        sample["track"] = tr.label;
                        sample["time"] = TimeValueToFrame(t);
                        if (ReadValue(tr.ctrl, t, sample["value"])) {
                            if (static_cast<int>(samples.size()) < budget.maxResults) samples.push_back(sample);
                        }
                    }
                }
            }

            return BuildKeyframeResult(
                action, nodes, tracks, budget,
                0, 0, 0, 0, 0, 0, 0, 0,
                samples, loopGaps, true).dump();
        }

        json styleParams = p;
        if (action == "set" && HasStyleRequest(styleParams) &&
            !styleParams.contains("time") && !styleParams.contains("times")) {
            styleParams["time"] = TimeValueToFrame(ip->GetTime());
            styleParams["time_unit"] = "frames";
        }

        std::vector<TrackRef> tracks;
        for (INode* node : nodes) CollectTracksForNode(node, p, tracks);
        if (tracks.empty()) {
            throw std::runtime_error("No matching animation tracks found.");
        }
        if (static_cast<int>(tracks.size()) > budget.maxTracks) {
            throw std::runtime_error(
                "Keyframe operation matched " + std::to_string(tracks.size()) +
                " tracks, over budget.max_tracks=" + std::to_string(budget.maxTracks) +
                ". Narrow names/tracks or raise budget.max_tracks.");
        }

        int styleKeyCount = 0;
        int styleControllerKeyCount = 0;
        if ((action == "style" || HasStyleRequest(styleParams)) && HasStyleRequest(styleParams)) {
            for (const TrackRef& tr : tracks) {
                if (action == "set") {
                    styleKeyCount += EstimateSetLogicalStyleCandidates(tr.ctrl, styleParams, ip);
                    styleControllerKeyCount += EstimateSetStyleControllerCandidates(tr.ctrl, styleParams, ip);
                } else {
                    styleKeyCount += CountLogicalStyleKeys(tr.ctrl, styleParams, ip);
                    styleControllerKeyCount += CountStyleKeys(tr.ctrl, styleParams, ip);
                }
            }
            if (styleControllerKeyCount > budget.maxKeys) {
                throw std::runtime_error(
                    "Style operation would touch " + std::to_string(styleControllerKeyCount) +
                    " controller keys, over budget.max_keys=" + std::to_string(budget.maxKeys) +
                    ". Narrow time/times/tracks or raise budget.max_keys for baked or mocap data.");
            }
        }

        int keyed = 0;
        int matched = 0;
        int styled = 0;
        int styledControllerKeys = 0;
        int ranged = 0;
        int rangedControllerEdits = 0;
        json samples = json::array();
        json loopGaps = json::array();

        if (action == "loop") {
            AnimateGuard anim;
            std::string unit = p.value("time_unit", "frames");
            TimeValue src = ToTimeValue(p.value("from_time", 1.0), unit);
            TimeValue dst = ToTimeValue(p.value("to_time", 100.0), unit);
            SortNodesByHierarchy(nodes);

            for (INode* node : nodes) {
                std::vector<TrackRef> nodeTracks;
                CollectTracksForNode(node, p, nodeTracks);
                for (const TrackRef& tr : nodeTracks) {
                    if (!tr.ctrl || UniqueKeyTimeCount(tr.ctrl) <= 0) continue;
                    if (WriteSampledValue(tr.ctrl, src, dst)) {
                        matched++;
                        json sample;
                        sample["object"] = WideToUtf8(tr.node->GetName());
                        sample["track"] = tr.label;
                        sample["from"] = TimeValueToFrame(src);
                        sample["to"] = TimeValueToFrame(dst);
                        ReadValue(tr.ctrl, dst, sample["value"]);
                        if (budget.includeSamples && static_cast<int>(samples.size()) < budget.maxResults) samples.push_back(sample);
                    }
                }
            }
        } else if (action == "set") {
            AnimateGuard anim;
            for (TimeValue t : TimesForSet(p, ip)) {
                for (const TrackRef& tr : tracks) {
                    bool wrote = HasValueRequest(p)
                        ? WriteRequestedValue(tr.ctrl, t, p, ComponentIndexFromLabel(tr.label))
                        : AddCurrentValueKey(tr.ctrl, t);
                    if (wrote) {
                        keyed++;
                        json sample;
                        sample["object"] = WideToUtf8(tr.node->GetName());
                        sample["track"] = tr.label;
                        sample["time"] = TimeValueToFrame(t);
                        ReadValue(tr.ctrl, t, sample["value"]);
                        if (budget.includeSamples && static_cast<int>(samples.size()) < budget.maxResults) samples.push_back(sample);
                    }
                }
            }
        } else if (action == "match") {
            AnimateGuard anim;
            std::string match = LowerCopy(p.value("match", "first_to_last"));
            std::string unit = p.value("time_unit", "frames");
            if (UsesHierarchyOrder(p, action)) SortNodesByHierarchy(nodes);

            auto runMatchForTracks = [&](const std::vector<TrackRef>& matchTracks) {
                for (const TrackRef& tr : matchTracks) {
                    TimeValue first = 0, last = 0;
                    if (!FirstLastTimes(tr.ctrl, first, last)) continue;

                    TimeValue src = first;
                    TimeValue dst = last;
                    if (match == "last_to_first" || match == "end_to_start") {
                        src = last;
                        dst = first;
                    }
                    if (p.contains("from_time") && JsonIsNumber(p["from_time"])) {
                        src = ToTimeValue(p["from_time"].get<double>(), unit);
                    }
                    if (p.contains("to_time") && JsonIsNumber(p["to_time"])) {
                        dst = ToTimeValue(p["to_time"].get<double>(), unit);
                    }

                    if (WriteSampledValue(tr.ctrl, src, dst)) {
                        matched++;
                        json sample;
                        sample["object"] = WideToUtf8(tr.node->GetName());
                        sample["track"] = tr.label;
                        sample["from"] = TimeValueToFrame(src);
                        sample["to"] = TimeValueToFrame(dst);
                        ReadValue(tr.ctrl, dst, sample["value"]);
                        if (budget.includeSamples && static_cast<int>(samples.size()) < budget.maxResults) samples.push_back(sample);
                    }
                }
            };

            if (UsesHierarchyOrder(p, action)) {
                for (INode* node : nodes) {
                    std::vector<TrackRef> nodeTracks;
                    CollectTracksForNode(node, p, nodeTracks);
                    runMatchForTracks(nodeTracks);
                }
            } else {
                runMatchForTracks(tracks);
            }
        } else if (action != "style" && action != "ort") {
            throw std::runtime_error("Unknown action: " + action + ". Use list, set, match, loop, style, or ort.");
        }

        if ((action == "style" || HasStyleRequest(styleParams)) && HasStyleRequest(styleParams)) {
            styleKeyCount = 0;
            styleControllerKeyCount = 0;
            for (const TrackRef& tr : tracks) {
                styleKeyCount += CountLogicalStyleKeys(tr.ctrl, styleParams, ip);
                styleControllerKeyCount += CountStyleKeys(tr.ctrl, styleParams, ip);
                int rawStyled = StyleOneController(tr.ctrl, styleParams, ip);
                if (rawStyled > 0) {
                    styled += CountLogicalStyleKeys(tr.ctrl, styleParams, ip);
                    styledControllerKeys += rawStyled;
                }
            }
        }
        if ((action == "ort" || HasOrtRequest(p)) && HasOrtRequest(p)) {
            for (const TrackRef& tr : tracks) {
                int rawRanged = ApplyOrtOneController(tr.ctrl, p);
                if (rawRanged > 0) {
                    ranged++;
                    rangedControllerEdits += rawRanged;
                }
            }
        }

        for (const TrackRef& tr : tracks) {
            if (tr.ctrl) tr.ctrl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        }
        ip->RedrawViews(ip->GetTime());

        if (p.contains("from_time") && JsonIsNumber(p["from_time"]) &&
            p.contains("to_time") && JsonIsNumber(p["to_time"])) {
            std::string unit = p.value("time_unit", "frames");
            TimeValue loopSrc = ToTimeValue(p["from_time"].get<double>(), unit);
            TimeValue loopDst = ToTimeValue(p["to_time"].get<double>(), unit);
            for (const TrackRef& tr : tracks) {
                if (!tr.ctrl || UniqueKeyTimeCount(tr.ctrl) <= 0) continue;
                json gap = LoopGapEntry(tr, loopSrc, loopDst);
                if (budget.includeSamples || !gap.value("matches", false)) {
                    loopGaps.push_back(gap);
                }
                if (static_cast<int>(loopGaps.size()) >= budget.maxResults) break;
            }
        }

        return BuildKeyframeResult(
            action, nodes, tracks, budget,
            keyed, matched, styled, styledControllerKeys,
            ranged, rangedControllerEdits,
            styleKeyCount, styleControllerKeyCount,
            samples, loopGaps, false).dump();
    });
}
