"""Public CTC ONNX exports by Sulabh Katiyar, derived from AI4Bharat models.

CC BY 4.0 export attribution: https://huggingface.co/sulabhkatiyar/indicconformer-120m-onnx
Original model attribution: https://github.com/AI4Bharat/IndicConformerASR
"""
from scripts.download_whisper_demo import download_public_model


if __name__ == '__main__':
    download_public_model('sulabhkatiyar/indicconformer-120m-onnx',
        '16f1697d0631c9bb88e371fca24577efe0aa8617',
        {'hi/model.onnx','hi/vocab.json','kn/model.onnx','kn/vocab.json'},'indicconformer-120m-onnx')
