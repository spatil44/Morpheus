#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2022-2023, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from unittest import mock

import numpy as np
import pytest

from _utils import TEST_DIRS
from _utils import calc_error_val
from _utils import mk_async_infer
from morpheus.config import PipelineModes
from morpheus.pipeline import LinearPipeline
from morpheus.stages.general.monitor_stage import MonitorStage
from morpheus.stages.inference.triton_inference_stage import TritonInferenceStage
from morpheus.stages.input.file_source_stage import FileSourceStage
from morpheus.stages.output.write_to_file_stage import WriteToFileStage
from morpheus.stages.postprocess.add_classifications_stage import AddClassificationsStage
from morpheus.stages.postprocess.serialize_stage import SerializeStage
from morpheus.stages.postprocess.validation_stage import ValidationStage
from morpheus.stages.preprocess.deserialize_stage import DeserializeStage
from morpheus.stages.preprocess.preprocess_nlp_stage import PreprocessNLPStage
from morpheus.utils.file_utils import load_labels_file

# End-to-end test intended to imitate the Phishing validation test
FEATURE_LENGTH = 128
MODEL_MAX_BATCH_SIZE = 32


@pytest.mark.slow
@pytest.mark.use_python
@mock.patch('tritonclient.grpc.InferenceServerClient')
def test_email_no_cpp(mock_triton_client, config, tmp_path):
    mock_metadata = {
        "inputs": [{
            "name": "input_ids", "datatype": "INT64", "shape": [-1, FEATURE_LENGTH]
        }, {
            "name": "attention_mask", "datatype": "INT64", "shape": [-1, FEATURE_LENGTH]
        }],
        "outputs": [{
            "name": "output", "datatype": "FP32", "shape": [-1, 2]
        }]
    }
    mock_model_config = {"config": {"max_batch_size": MODEL_MAX_BATCH_SIZE}}

    mock_triton_client.return_value = mock_triton_client
    mock_triton_client.is_server_live.return_value = True
    mock_triton_client.is_server_ready.return_value = True
    mock_triton_client.is_model_ready.return_value = True
    mock_triton_client.get_model_metadata.return_value = mock_metadata
    mock_triton_client.get_model_config.return_value = mock_model_config

    data = np.loadtxt(os.path.join(TEST_DIRS.tests_data_dir, 'triton_phishing_inf_results.csv'), delimiter=',')
    inf_results = np.split(data, range(MODEL_MAX_BATCH_SIZE, len(data), MODEL_MAX_BATCH_SIZE))

    async_infer = mk_async_infer(inf_results)

    mock_triton_client.async_infer.side_effect = async_infer

    config.mode = PipelineModes.NLP
    config.class_labels = load_labels_file(os.path.join(TEST_DIRS.data_dir, "labels_phishing.txt"))
    config.model_max_batch_size = MODEL_MAX_BATCH_SIZE
    config.pipeline_batch_size = 1024
    config.feature_length = FEATURE_LENGTH
    config.edge_buffer_size = 128
    config.num_threads = 1

    val_file_name = os.path.join(TEST_DIRS.validation_data_dir, 'phishing-email-validation-data.jsonlines')
    vocab_file_name = os.path.join(TEST_DIRS.data_dir, 'bert-base-uncased-hash.txt')
    out_file = os.path.join(tmp_path, 'results.csv')
    results_file_name = os.path.join(tmp_path, 'results.json')

    pipe = LinearPipeline(config)
    pipe.set_source(FileSourceStage(config, filename=val_file_name, iterative=False))
    pipe.add_stage(DeserializeStage(config))
    pipe.add_stage(
        PreprocessNLPStage(config,
                           vocab_hash_file=vocab_file_name,
                           truncation=True,
                           do_lower_case=True,
                           add_special_tokens=False))
    pipe.add_stage(
        TritonInferenceStage(config, model_name='phishing-bert-onnx', server_url='test:0000',
                             force_convert_inputs=True))
    pipe.add_stage(MonitorStage(config, description="Inference Rate", smoothing=0.001, unit="inf"))
    pipe.add_stage(AddClassificationsStage(config, labels=["is_phishing"], threshold=0.7))
    pipe.add_stage(
        ValidationStage(config, val_file_name=val_file_name, results_file_name=results_file_name, rel_tol=0.05))
    pipe.add_stage(SerializeStage(config))
    pipe.add_stage(WriteToFileStage(config, filename=out_file, overwrite=False))

    pipe.run()
    results = calc_error_val(results_file_name)
    assert results.diff_rows == 153


@pytest.mark.slow
@pytest.mark.use_cpp
@pytest.mark.usefixtures("launch_mock_triton")
def test_email_cpp(config, tmp_path):
    config.mode = PipelineModes.NLP
    config.class_labels = load_labels_file(os.path.join(TEST_DIRS.data_dir, "labels_phishing.txt"))
    config.model_max_batch_size = MODEL_MAX_BATCH_SIZE
    config.pipeline_batch_size = 1024
    config.feature_length = FEATURE_LENGTH
    config.edge_buffer_size = 128
    config.num_threads = 1

    val_file_name = os.path.join(TEST_DIRS.validation_data_dir, 'phishing-email-validation-data.jsonlines')
    vocab_file_name = os.path.join(TEST_DIRS.data_dir, 'bert-base-uncased-hash.txt')
    out_file = os.path.join(tmp_path, 'results.csv')
    results_file_name = os.path.join(tmp_path, 'results.json')

    pipe = LinearPipeline(config)
    pipe.set_source(FileSourceStage(config, filename=val_file_name, iterative=False))
    pipe.add_stage(DeserializeStage(config))
    pipe.add_stage(
        PreprocessNLPStage(config,
                           vocab_hash_file=vocab_file_name,
                           truncation=True,
                           do_lower_case=True,
                           add_special_tokens=False))
    pipe.add_stage(
        TritonInferenceStage(config,
                             model_name='phishing-bert-onnx',
                             server_url='localhost:8001',
                             force_convert_inputs=True))
    pipe.add_stage(MonitorStage(config, description="Inference Rate", smoothing=0.001, unit="inf"))
    pipe.add_stage(AddClassificationsStage(config, labels=["is_phishing"], threshold=0.7))
    pipe.add_stage(
        ValidationStage(config, val_file_name=val_file_name, results_file_name=results_file_name, rel_tol=0.05))
    pipe.add_stage(SerializeStage(config))
    pipe.add_stage(WriteToFileStage(config, filename=out_file, overwrite=False))

    pipe.run()
    results = calc_error_val(results_file_name)
    assert results.diff_rows == 682
