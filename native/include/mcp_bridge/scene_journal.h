#pragma once

#include <cstddef>
#include <cstdint>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace SceneJournal {

void Register();
void Unregister();
void Reset();

bool IsRegistered();
unsigned long long CurrentSeq();
json ChangesSince(unsigned long long since, size_t limit = 256);

} // namespace SceneJournal
