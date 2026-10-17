import unittest
import importlib.util
from types import SimpleNamespace

if importlib.util.find_spec("torch") is None:
    raise unittest.SkipTest("VITS tests run in the speech container with PyTorch")

import torch

from scripts.vits_dynamic import FullLengthVits


class FakeExport(torch.nn.Module):
    code_with_constants = ("", SimpleNamespace(const_mapping={
        "c0": torch.tensor([105]), "c1": torch.tensor(1), "c2": torch.tensor(0)}))

    def text_encoder(self, tokens, lengths):
        mask = (torch.arange(tokens.shape[1]).unsqueeze(0) < lengths.unsqueeze(1)).unsqueeze(1).float()
        means = tokens.unsqueeze(1).float() * mask
        return means, mask, means, torch.zeros_like(means)

    def duration_predictor(self, encoded, mask):
        return torch.zeros_like(encoded)

    def flow(self, latent, mask):
        return latent

    def waveform_decoder(self, latent):
        return latent


class FullLengthVitsTests(unittest.TestCase):
    def test_retains_tokens_beyond_export_sample_length(self):
        model = FullLengthVits(FakeExport())
        for length in (20, 105, 237):
            tokens = torch.arange(1, length + 1).unsqueeze(0)
            output = model(tokens)
            self.assertEqual(output.shape, (1, 1, length))
            torch.testing.assert_close(output.squeeze(), tokens.squeeze().float())

    def test_preserves_export_length_scale_and_batch_alignment(self):
        exported = FakeExport()
        exported.code_with_constants = ("", SimpleNamespace(const_mapping={
            "c0": torch.tensor([105]), "c1": torch.tensor(2), "c2": torch.tensor(0)}))
        output = FullLengthVits(exported)(torch.tensor([[1, 2, 3], [4, 5, 6]]))
        torch.testing.assert_close(output[:, 0], torch.tensor([
            [1, 1, 2, 2, 3, 3], [4, 4, 5, 5, 6, 6]], dtype=torch.float))


if __name__ == "__main__":
    unittest.main()
