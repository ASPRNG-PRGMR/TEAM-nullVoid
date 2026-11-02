#include "link.h"

namespace bb {

cfg::Bin resolveBin(const Verdict& v) {
  if (!v.ok) return cfg::kFallbackBin;
  if (v.bin != cfg::kBinRecyclable && v.bin != cfg::kBinNonRecyclable) {
    return cfg::kFallbackBin;
  }
  if (v.confidence < cfg::kMinConfidence) return cfg::kFallbackBin;
  return v.bin;
}

}  // namespace bb
