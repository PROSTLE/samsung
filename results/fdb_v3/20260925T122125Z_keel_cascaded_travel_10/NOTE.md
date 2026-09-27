# Stopped before any scenario ran

FDB-v3's runner ran out of GPU memory while loading its ASR model on the development
laptop's 4 GB GPU (`torch.OutOfMemoryError: CUDA out of memory`, end of `inference.log`),
so no scenario ran and there are no reports. Kept as the record of that attempt; the
later runs in this folder are the ones with results.
