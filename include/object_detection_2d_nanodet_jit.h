/*
 * Copyright 2020-2022 OpenDR European Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef C_API_NANODET_H
#define C_API_NANODET_H

#include "opendr_utils.h"
#include "target.h"

#ifdef __cplusplus
extern "C" {
#endif

struct nanodet_model {
  // Jit cpp class holder
  void *network;

  // Device to be used
  char *device;

  // Recognition threshold
  float score_threshold;

  // Model input size
  int input_size[2];

  // Keep ratio flag
  int keep_ratio;
};
typedef struct nanodet_model nanodet_model_t;

/**
 * Loads a nanodet object detection model saved in libtorch format.
 * @param modelPath path to the libtorch nanodet model (as exported using OpenDR)
 * @param device the device that will be used for inference
 * @param height the height of model input
 * @param width the width of model input
 * @param scoreThreshold confidence threshold
 * @param model the model to be loaded
 */
void load_nanodet_model(char *modelPath, char *device, int height, int width, float scoreThreshold, nanodet_model_t *model);

/**
 * This function performs inference using a nanodet object detection model and an input image.
 * @param model nanodet model to be used for inference
 * @param image OpenDR image
 * @return OpenDR detection vector target containing the detections of the recognized objects
 */
opendr_detection_vector_target_t infer_nanodet(nanodet_model_t *model, opendr_image_t *image);

/**
 * Releases the memory allocated for a nanodet object detection model.
 * @param model model to be de-allocated
 */
void free_nanodet_model(nanodet_model_t *model);

/**
 * Draw the bounding boxes from detections in the given image.
 * @param image image that has been used for inference
 * @param model nanodet model that has been used for inference
 * @param detectionsVector output of the inference
 */
void draw_bboxes(opendr_image_t *image, nanodet_model_t *model, opendr_detection_vector_target_t *detectionsVector);

#ifdef __cplusplus
}
#endif

#endif  // C_API_NANODET_H