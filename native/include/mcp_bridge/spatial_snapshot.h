#pragma once

#include <max.h>
#include <nlohmann/json.hpp>
#include <string>

using json = nlohmann::json;

namespace SpatialSnapshot {

enum class PosMode { Pivot, Center, Ground };

json PointJson(const Point3& p);
Box3 WorldBoundingBox(INode* node, TimeValue t);
Point3 BottomCenter(const Box3& bbox);
Point3 BBoxCenter(const Box3& bbox);

PosMode ParsePosMode(const std::string& mode);
const char* PosModeToString(PosMode mode);

json SpaceJson();
json UnitsJson();
json TypeAxisHints(const std::string& type);
json NodeOrientationJson(INode* node, TimeValue t);

void ApplyPosMode(INode* node, TimeValue t, const Point3& targetPos, bool hasTargetPos, PosMode mode);

json BuildSpatialSnapshot(INode* node, TimeValue t, const std::string& typeHint = "");
json BuildCreateObjectResult(
    INode* node,
    TimeValue t,
    const std::string& requestedType,
    const Point3& requestedPos,
    bool hasRequestedPos,
    PosMode mode);

} // namespace SpatialSnapshot
