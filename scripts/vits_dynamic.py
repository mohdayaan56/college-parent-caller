"""Run VoiceAPI's traced VITS submodules with the actual input length.

The exported top-level forward bakes the export sample's token count into
x_lengths (105 for the English voice). Its encoder then masks later tokens.
Keep the exported weights and submodules, but rebuild the top-level alignment
with dynamic lengths and the export's own length/noise scales.
"""
import torch
from torch.nn import functional as F


class FullLengthVits(torch.nn.Module):
    def __init__(self, exported):
        super().__init__()
        self.exported = exported
        constants = exported.code_with_constants[1].const_mapping
        if not all(key in constants for key in ("c0", "c1", "c2")):
            raise ValueError("Unsupported VITS export; cannot recover inference scales")
        self.length_scale = float(constants["c1"])
        self.noise_scale = float(constants["c2"])

    def latent(self, tokens):
        """Sample once for the entire text; decoder windows share this tensor."""
        lengths = torch.full((tokens.shape[0],), tokens.shape[1],
                             dtype=torch.long, device=tokens.device)
        encoded, x_mask, means, log_scales = self.exported.text_encoder(tokens, lengths)
        log_durations = self.exported.duration_predictor(encoded, x_mask)
        durations = torch.ceil(torch.exp(log_durations) * x_mask * self.length_scale)
        output_lengths = torch.clamp_min(durations.sum(dim=(1, 2)), 1).long()
        frame_range = torch.arange(int(output_lengths.max()), device=tokens.device)
        y_mask = (frame_range.unsqueeze(0) < output_lengths.unsqueeze(1)).unsqueeze(1).to(x_mask.dtype)
        alignment_mask = x_mask.transpose(1, 2) * y_mask
        cumulative = durations.squeeze(1).cumsum(1)
        path = (frame_range.view(1, 1, -1) < cumulative.unsqueeze(-1)).to(x_mask.dtype)
        path = (path - F.pad(path, (0, 0, 1, 0))[:, :-1, :]) * alignment_mask
        aligned_means = torch.matmul(means, path)
        aligned_scales = torch.matmul(log_scales, path)
        latent = aligned_means + torch.randn_like(aligned_means) * torch.exp(aligned_scales) * self.noise_scale
        return self.exported.flow(latent, y_mask) * y_mask

    def forward(self, tokens):
        return self.exported.waveform_decoder(self.latent(tokens))
