import time
import sys
import os
import torch
import fire
from pathlib import Path
from opendr.engine.target import TrackingAnnotation3D, TrackingAnnotation3DList
from opendr.perception.object_detection_3d.voxel_object_detection_3d.second_detector.utils.eval import (
    d3_box_overlap,
)
from opendr.perception.object_tracking_3d.datasets.kitti_siamese_tracking import (
    SiameseTrackingDatasetIterator,
    SiameseTripletTrackingDatasetIterator,
)
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.metrics import (
    Precision,
    Success,
)
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.realtime_evaluator import RealTimeEvaluator
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.second_detector.run import (
    iou_2d,
    tracking_boxes_to_lidar,
)
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.vpit_object_tracking_3d_learner import (
    VpitObjectTracking3DLearner,
)
from opendr.perception.object_detection_3d.datasets.kitti import (
    KittiDataset,
    LabeledPointCloudsDatasetIterator,
)
from opendr.perception.object_tracking_3d.datasets.kitti_tracking import (
    KittiTrackingDatasetIterator,
    LabeledTrackingPointCloudsDatasetIterator,
)
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.draw import (
    AverageMetric,
    draw_point_cloud_bev,
    draw_point_cloud_projected_2,
    stack_images,
)
from PIL import Image as PilImage
import numpy as np
from opendr.perception.object_tracking_3d.single_object_tracking.vpit.second_detector.core.box_np_ops import (
    box_camera_to_lidar,
    box_lidar_to_camera,
    camera_to_lidar,
    center_to_corner_box3d,
)
import cv2


DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"

dataset_detection_path = "/data/sets/kitti_second"
dataset_tracking_path = "/data/sets/kitti_tracking"

temp_dir = os.path.join(
    "tests",
    "sources",
    "tools",
    "perception",
    "object_detection_3d",
    "voxel_object_detection_3d",
    "voxel_object_detection_3d_temp",
)

config_tanet_car = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/tanet/car/xyres_16.proto"
config_tanet_ped_cycle = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/tanet/ped_cycle/xyres_16.proto"
config_pointpillars_car = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/pointpillars/car/xyres_16.proto"
config_pointpillars_car_tracking = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/pointpillars/car/xyres_16_tracking.proto"
config_pointpillars_car_tracking_s = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/pointpillars/car/xyres_16_tracking_s.proto"
config_tanet_car_tracking = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/tanet/car/xyres_16_tracking.proto"
config_tanet_car_tracking_s = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/tanet/car/xyres_16_tracking_s.proto"
config_pointpillars_ped_cycle = "src/opendr/perception/object_tracking_3d/single_object_tracking/vpit/second_detector/configs/pointpillars/ped_cycle/xyres_16.proto"

subsets_path = os.path.join(
    ".",
    "src",
    "opendr",
    "perception",
    "object_detection_3d",
    "datasets",
    "kitti_subsets",
)

model_paths = {
    "tanet_car": "models/tanet_car_xyres_16",
    "tanet_ped_cycle": "models/tanet_ped_cycle_xyres_16",
    "pointpillars_car": "models/pointpillars_car_xyres_16",
    "pointpillars_ped_cycle": "models/pointpillars_ped_cycle_xyres_16",
}

all_configs = {
    "tanet_car": config_tanet_car,
    "tanet_ped_cycle": config_tanet_ped_cycle,
    "pointpillars_car": config_pointpillars_car,
    "pointpillars_ped_cycle": config_pointpillars_ped_cycle,
}
car_configs = {
    "tanet_car": config_tanet_car,
    "pointpillars_car": config_pointpillars_car,
}

# kitti_detection = KittiDataset(dataset_detection_path)
# dataset_detection = LabeledPointCloudsDatasetIterator(
#     dataset_detection_path + "/training/velodyne_reduced",
#     dataset_detection_path + "/training/label_2",
#     dataset_detection_path + "/training/calib",
# )
track_id = "0000"
dataset_tracking = LabeledTrackingPointCloudsDatasetIterator(
    dataset_tracking_path + "/training/velodyne/" + track_id,
    dataset_tracking_path + "/training/label_02/" + track_id + ".txt",
    dataset_tracking_path + "/training/calib/" + track_id + ".txt",
)
name = "pointpillars_car"
config = all_configs[name]
model_path = model_paths[name]


tanet_name = "tanet_car"
tanet_config = all_configs[tanet_name]
tanet_model_path = model_paths[tanet_name]

backbone_configs = {
    "pp": config_pointpillars_car,
    "spp": config_pointpillars_car_tracking,
    "spps": config_pointpillars_car_tracking_s,
    "tanet": config_tanet_car,
    "stanet": config_tanet_car_tracking,
    "stanets": config_tanet_car_tracking_s,
}
backbone_model_paths = {
    "pp": "models/pointpillars_car_xyres_16",
    "spp": "models/pointpillars_tracking",
    "spps": "models/pointpillars_tracking_s",
    "tanet": "models/tanet_car_xyres_16",
    "stanet": "models/tanet_tracking",
    "stanets": "models/tanet_tracking_s",
}


pq = 1
lq = 20


def estimate_accuracy(box_a, box_b, dim=3):
    if dim == 3:
        return np.linalg.norm(box_a.location - box_b.location, ord=2)
    elif dim == 2:
        return np.linalg.norm(box_a.location[[0, 1]] - box_b.location[[0, 1]], ord=2)


def tracking_boxes_to_camera(
    label_original, calib,
):

    label = label_original.kitti()

    if len(label["name"]) <= 0:
        return label_original

    r0_rect = calib["R0_rect"]
    trv2c = calib["Tr_velo_to_cam"]

    dims = label["dimensions"]
    locs = label["location"]
    rots = label["rotation_y"]

    boxes_lidar = np.concatenate([locs, dims, rots.reshape(-1, 1)], axis=1)
    boxes_camera = box_lidar_to_camera(boxes_lidar, r0_rect, trv2c)
    locs_camera = boxes_camera[:, 0:3]
    dims_camera = boxes_camera[:, 3:6]
    rots_camera = boxes_camera[:, 6:7]

    new_label = {
        "name": label["name"],
        "truncated": label["truncated"],
        "occluded": label["occluded"],
        "alpha": label["alpha"],
        "bbox": label["bbox"],
        "dimensions": dims_camera,
        "location": locs_camera,
        "rotation_y": rots_camera,
        "score": label["score"],
        "id": label["id"]
        if "id" in label
        else np.array(list(range(len(label["name"])))),
        "frame": label["frame"]
        if "frame" in label
        else np.array([0] * len(label["name"])),
    }

    result = TrackingAnnotation3DList.from_kitti(
        new_label, new_label["id"], new_label["frame"]
    )

    return result


def label_to_AABB(label):

    if len(label) == 0:
        return label

    label = label.kitti()

    dims = label["dimensions"]
    locs = label["location"]
    rots = label["rotation_y"]

    origin = [0.5, 0.5, 0]
    gt_corners = center_to_corner_box3d(
        locs, dims, rots.reshape(-1), origin=origin, axis=2,
    )

    mins = np.min(gt_corners, axis=1)
    maxs = np.max(gt_corners, axis=1)
    centers = (maxs + mins) / 2
    sizes = maxs - mins
    rotations = np.zeros((centers.shape[0],), dtype=np.float32)

    new_label = {
        "name": label["name"],
        "truncated": label["truncated"],
        "occluded": label["occluded"],
        "alpha": label["alpha"],
        "bbox": label["bbox"],
        "dimensions": sizes,
        "location": centers,
        "rotation_y": rotations,
        "score": label["score"],
        "id": label["id"],
        "frame": label["frame"],
    }

    result = TrackingAnnotation3DList.from_kitti(
        new_label, new_label["id"], new_label["frame"]
    )

    return result


def test_eval_detection():
    print("Eval", name, "start", file=sys.stderr)
    model_path = model_paths[name]

    learner = VpitObjectTracking3DLearner(model_config_path=config, device=DEVICE)
    learner.load(model_path)
    mAPbbox, mAPbev, mAP3d, mAPaos = learner.eval(dataset_detection)

    print(
        "Ok?", mAPbbox[0][0][0] > 70 and mAPbbox[0][0][0] < 95,
    )


def test_draw_tracking_dataset():
    import pygifsicle
    import imageio

    for track_id in [
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0004",
        "0003",
        "0002",
        "0001",
        "0000",
    ]:
        dataset_tracking = LabeledTrackingPointCloudsDatasetIterator(
            dataset_tracking_path + "/training/velodyne/" + track_id,
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt",
            dataset_tracking_path + "/training/calib/" + track_id + ".txt",
        )
        images = []
        filename = "./plots/video/dataset_" + track_id + ".gif"

        for i in range(len(dataset_tracking)):
            print("track_id", track_id, i, "/", len(dataset_tracking))
            point_cloud_with_calibration, label = dataset_tracking[i]
            point_cloud = point_cloud_with_calibration.data
            calib = point_cloud_with_calibration.calib
            lidar_boxes = tracking_boxes_to_lidar(label, calib)
            image = draw_point_cloud_bev(point_cloud, lidar_boxes)
            images.append(image)

        imageio.mimsave(filename, images)
        pygifsicle.optimize(filename)


def test_draw_siamese_tracking_dataset():

    track_ids = ["0000", "0002", "0003"]

    dataset_siamese_tracking = SiameseTrackingDatasetIterator(
        [
            dataset_tracking_path + "/training/velodyne/" + track_id
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt"
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/calib/" + track_id + ".txt"
            for track_id in track_ids
        ],
    )

    for q in range(lq):  # range(len(dataset_siamese_tracking)):
        i = q * pq
        print(i, "/", len(dataset_tracking))
        (
            target_point_cloud_calib,
            search_point_cloud_calib,
            target_label,
            search_label,
        ) = dataset_siamese_tracking[i]
        target_point_cloud = target_point_cloud_calib.data
        search_point_cloud = search_point_cloud_calib.data
        calib = target_point_cloud_calib.calib
        target_lidar_boxes = tracking_boxes_to_lidar(target_label, calib)
        search_lidar_boxes = tracking_boxes_to_lidar(search_label, calib)
        image_target = draw_point_cloud_bev(target_point_cloud, target_lidar_boxes)
        image_search = draw_point_cloud_bev(search_point_cloud, search_lidar_boxes)
        PilImage.fromarray(image_target).save("./plots/std_target_" + str(i) + ".png")
        PilImage.fromarray(image_search).save("./plots/std_search_" + str(i) + ".png")
        print()


def test_draw_detection_dataset():

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q
        print(i, "/", len(dataset_detection))
        point_cloud_with_calibration, label = dataset_detection[i]
        point_cloud = point_cloud_with_calibration.data
        calib = point_cloud_with_calibration.calib
        lidar_boxes = tracking_boxes_to_lidar(label, calib)
        image = draw_point_cloud_bev(point_cloud, lidar_boxes)
        PilImage.fromarray(image).save("./plots/kd_" + str(i) + ".png")


def test_draw_detection_projected():

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q
        print(i, "/", len(dataset_detection))
        point_cloud_with_calibration, label = dataset_detection[i]
        point_cloud = point_cloud_with_calibration.data
        calib = point_cloud_with_calibration.calib
        lidar_boxes = tracking_boxes_to_lidar(label, calib)
        image = draw_point_cloud_projected_2(point_cloud, lidar_boxes)
        PilImage.fromarray(image).save("./plots/dpr_" + str(i) + ".png")


def test_draw_tracking_projected():

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q * pq
        print(i, "/", len(dataset_tracking))
        point_cloud_with_calibration, label = dataset_tracking[i]
        point_cloud = point_cloud_with_calibration.data
        calib = point_cloud_with_calibration.calib
        lidar_boxes = tracking_boxes_to_lidar(label, calib)
        image = draw_point_cloud_projected_2(point_cloud, lidar_boxes)
        PilImage.fromarray(image).save("./plots/pr_" + str(i) + ".png")


def test_draw_tracking_aabb():

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q * pq
        print(i, "/", len(dataset_tracking))
        point_cloud_with_calibration, label = dataset_tracking[i]
        point_cloud = point_cloud_with_calibration.data
        calib = point_cloud_with_calibration.calib
        lidar_boxes = tracking_boxes_to_lidar(label, calib)
        aabb = label_to_AABB(lidar_boxes)
        image = draw_point_cloud_bev(point_cloud, aabb)
        PilImage.fromarray(image).save("./plots/aabb_" + str(i) + ".png")


def test_pp_infer_tracking():
    print("Eval", name, "start", file=sys.stderr)

    learner = VpitObjectTracking3DLearner(model_config_path=config, device=DEVICE)
    learner.load(model_path)

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q * pq
        print(i, "/", len(dataset_tracking))
        point_cloud_with_calibration, label = dataset_tracking[i]
        predictions = learner.infer(point_cloud_with_calibration)
        image = draw_point_cloud_bev(point_cloud_with_calibration.data, predictions)
        PilImage.fromarray(image).save("./plots/pp_" + str(i) + ".png")


def test_pp_infer_detection():
    print("Eval", name, "start", file=sys.stderr)

    learner = VpitObjectTracking3DLearner(model_config_path=config, device=DEVICE)
    learner.load(model_path)

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q * pq
        print(i, "/", len(dataset_tracking))
        point_cloud_with_calibration, label = dataset_detection[i]
        predictions = learner.infer(point_cloud_with_calibration)
        image = draw_point_cloud_bev(point_cloud_with_calibration.data, predictions)
        PilImage.fromarray(image).save("./plots/dpp_" + str(i) + ".png")


def test_tanet_infer_tracking():
    print("Eval", tanet_name, "start", file=sys.stderr)

    learner = VpitObjectTracking3DLearner(
        model_config_path=tanet_config, device=DEVICE
    )

    if not os.path.exists(tanet_model_path):
        learner.download("tanet_car_xyres_16", "models")

    learner.load(tanet_model_path)

    for q in range(lq):  # range(len(dataset_tracking)):\
        i = q * pq
        print(i, "/", len(dataset_tracking))
        point_cloud_with_calibration, label = dataset_tracking[i]
        predictions = learner.infer(point_cloud_with_calibration)
        image = draw_point_cloud_bev(point_cloud_with_calibration.data, predictions)
        PilImage.fromarray(image).save("./plots/tanet_" + str(i) + ".png")


def test_pp_siamese_fit(
    model_name,
    load=0,
    steps=0,
    debug=False,
    device=DEVICE,
    checkpoint_after_iter=1000,
    lr=0.0001,
    backbone="pp",
    load_backbone=True,
    **kwargs,
):
    print("Fit", name, "start", file=sys.stderr)
    print("Using device:", device)

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        lr=lr,
        checkpoint_after_iter=checkpoint_after_iter,
        checkpoint_load_iter=load,
        backbone=backbone,
        **kwargs,
    )
    if load_backbone:
        learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    learner.fit(
        kitti_detection,
        model_dir="./temp/" + model_name,
        debug=debug,
        steps=steps,
        # verbose=True
    )

    print()


def test_pp_siamese_fit_siamese_training(
    model_name=None,
    load=0,
    steps=0,
    debug=False,
    device=DEVICE,
    checkpoint_after_iter=1000,
    lr=0.0001,
    backbone="pp",
    track_ids=[
        "0000",
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0012",
        "0013",
        "0014",
        "0015",
        "0016",
    ],
    validation_track_ids=[
        "0017",
        "0018",
    ],
    load_optimizer=True,
    load_backbone=True,
    params_file=None,
    **kwargs,
):
    print("Fit", name, "start", file=sys.stderr)
    print("Using device:", device)

    if params_file is not None:
        params = load_params_from_file(params_file)
        model_name = params["model_name"] if "model_name" in params else model_name
        device = params["device"] if "device" in params else device
        backbone = params["backbone"] if "backbone" in params else backbone

        for k, v in params.items():
            if (
                k
                not in [
                    "model_name",
                    "load",
                    "device",
                    "backbone",
                    "lr",
                ]
            ) and (k not in kwargs):
                kwargs[k] = v

    dataset_siamese_tracking = SiameseTrackingDatasetIterator(
        [
            dataset_tracking_path + "/training/velodyne/" + track_id
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt"
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/calib/" + track_id + ".txt"
            for track_id in track_ids
        ],
    )

    val_dataset_siamese_tracking = SiameseTrackingDatasetIterator(
        [
            dataset_tracking_path + "/training/velodyne/" + track_id
            for track_id in validation_track_ids
        ],
        [
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt"
            for track_id in validation_track_ids
        ],
        [
            dataset_tracking_path + "/training/calib/" + track_id + ".txt"
            for track_id in validation_track_ids
        ],
    )

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        lr=lr,
        checkpoint_after_iter=checkpoint_after_iter,
        checkpoint_load_iter=load,
        backbone=backbone,
        **kwargs,
    )
    if load_backbone:
        learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    learner.fit(
        dataset_siamese_tracking,
        val_dataset=val_dataset_siamese_tracking,
        model_dir="./temp/" + model_name,
        debug=debug,
        steps=steps,
        load_optimizer=load_optimizer,
        # verbose=True
    )

    print()


def test_pp_siamese_fit_siamese_triplet_training(
    model_name,
    load=0,
    steps=0,
    debug=False,
    device=DEVICE,
    checkpoint_after_iter=1000,
    lr=0.0001,
    backbone="pp",
    track_ids=[
        "0000",
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0012",
        "0013",
        "0014",
        "0015",
        "0016",
    ],
    **kwargs,
):
    print("Fit", name, "start", file=sys.stderr)
    print("Using device:", device)

    dataset_siamese_tracking = SiameseTripletTrackingDatasetIterator(
        [
            dataset_tracking_path + "/training/velodyne/" + track_id
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt"
            for track_id in track_ids
        ],
        [
            dataset_tracking_path + "/training/calib/" + track_id + ".txt"
            for track_id in track_ids
        ],
    )

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        lr=lr,
        checkpoint_after_iter=checkpoint_after_iter,
        checkpoint_load_iter=load,
        backbone=backbone,
        **kwargs,
    )
    learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    learner.fit(
        dataset_siamese_tracking,
        model_dir="./temp/" + model_name,
        debug=debug,
        steps=steps,
        # verbose=True
    )

    print()


def test_rotated_pp_siamese_infer(
    model_name=None,
    load=0,
    classes=["Car", "Van", "Truck"],
    draw=True,
    iou_min=0.5,
    device=DEVICE,
    backbone="pp",
    params_file=None,
    object_ids=[0],  # [0, 3]
    start_frame=0,
    track_id="0000",
    **kwargs,
):

    if params_file is not None:
        params = load_params_from_file(params_file)
        model_name = params["model_name"] if "model_name" in params else model_name
        if load == 0:
            load = params["load"] if "load" in params else load
        draw = params["draw"] if "draw" in params else draw
        iou_min = params["iou_min"] if "iou_min" in params else iou_min
        classes = params["classes"] if "classes" in params else classes
        device = params["device"] if "device" in params else device
        backbone = params["backbone"] if "backbone" in params else backbone

        for k, v in params.items():
            if (
                k
                not in [
                    "model_name",
                    "load",
                    "draw",
                    "iou_min",
                    "classes",
                    "tracks",
                    "device",
                    "eval_id",
                    "near_distance",
                    "backbone",
                    "raise_on_infer_error",
                    "limit_object_ids",
                ]
            ) and (k not in kwargs):
                kwargs[k] = v

    print("Infer", name, "start", file=sys.stderr)
    import pygifsicle
    import imageio

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        backbone=backbone,
        checkpoint_after_iter=2000,
        **kwargs,
    )

    checkpoints_path = "./temp/" + model_name + "/checkpoints"

    if load == 0:
        learner.load(checkpoints_path, backbone=False, verbose=True)
    elif load == "pretrained":
        learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    else:
        learner.load_from_checkpoint(checkpoints_path, load)

    dataset = LabeledTrackingPointCloudsDatasetIterator(
        dataset_tracking_path + "/training/velodyne/" + track_id,
        dataset_tracking_path + "/training/label_02/" + track_id + ".txt",
        dataset_tracking_path + "/training/calib/" + track_id + ".txt",
    )

    total_success = Success()
    total_precision = Precision()
    total_success_ideal = Success()
    total_precision_ideal = Precision()
    total_success_same = Success()
    total_precision_same = Precision()

    all_mean_iou3ds = []
    all_mean_iouAabbs = []
    all_tracked = []
    all_precision = []
    all_success = []
    vertical_error = AverageMetric()
    vertical_error_no_regress = AverageMetric()
    all_vertical_error = []
    all_vertical_error_no_regress = []

    count = len(dataset)

    def test_object_id(object_id, start_frame=-1):

        selected_labels = []

        object_success = Success()
        object_precision = Precision()

        while len(selected_labels) <= 0:
            start_frame += 1
            if start_frame >= len(dataset):
                return None, None
            point_cloud_with_calibration, labels = dataset[start_frame]
            selected_labels = TrackingAnnotation3DList(
                [label for label in labels if (label.id == object_id)]
            )

        if not selected_labels[0].name in classes:
            return None, None, None, None, None

        total_precision.add_accuracy(0.0)
        total_success.add_overlap(1.0)
        total_precision_ideal.add_accuracy(0.0)
        total_success_ideal.add_overlap(1.0)
        object_precision.add_accuracy(0.0)
        object_success.add_overlap(1.0)
        object_vertical_error = AverageMetric()
        object_vertical_error_no_regress = AverageMetric()

        calib = point_cloud_with_calibration.calib
        labels_lidar = tracking_boxes_to_lidar(selected_labels, calib, classes=classes)
        label_lidar = labels_lidar[0]

        learner.init(point_cloud_with_calibration, label_lidar, draw=draw)

        images = []
        images_small = []
        ious = []
        count_tracked = 0

        for i in range(start_frame, count):
            point_cloud_with_calibration, labels = dataset[i]
            selected_labels = TrackingAnnotation3DList(
                [label for label in labels if label.id == object_id]
            )

            if len(selected_labels) <= 0:
                break

            calib = point_cloud_with_calibration.calib
            labels_lidar = tracking_boxes_to_lidar(selected_labels, calib)
            label_lidar = labels_lidar[0] if len(labels_lidar) > 0 else None

            result = learner.infer(
                point_cloud_with_calibration, id=-1, frame=i, draw=draw,
            )

            label_lidar.data["name"] = "Target"

            all_labels = (
                result
                if label_lidar is None
                else TrackingAnnotation3DList([result[0], label_lidar])
            )
            image = draw_point_cloud_bev(point_cloud_with_calibration.data, all_labels)
            image_small = draw_point_cloud_bev(
                point_cloud_with_calibration.data, all_labels, scale=1
            )

            if draw:
                pil_image = PilImage.fromarray(image)
                pil_image_small = PilImage.fromarray(image_small)
                images.append(pil_image)
                images_small.append(pil_image_small)

            result_ideal = TrackingAnnotation3D(
                result[0].name,
                result[0].truncated,
                result[0].occluded,
                result[0].alpha,
                result[0].bbox2d,
                result[0].dimensions,
                np.array(
                    [*result[0].location[:-1], label_lidar.location[-1]]
                ),
                result[0].rotation_y,
                result[0].id,
                1,
                result[0].frame,
            )
            label_same = TrackingAnnotation3D(
                label_lidar.name,
                label_lidar.truncated,
                label_lidar.occluded,
                label_lidar.alpha,
                label_lidar.bbox2d,
                label_lidar.dimensions,
                label_lidar.location,
                label_lidar.rotation_y,
                label_lidar.id,
                1,
                label_lidar.frame,
            )

            vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
            vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))
            object_vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
            object_vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))

            result = tracking_boxes_to_camera(result, calib)[0]
            label_lidar = tracking_boxes_to_camera(
                TrackingAnnotation3DList([label_lidar]), calib
            )[0]
            result_ideal = tracking_boxes_to_camera(
                TrackingAnnotation3DList([result_ideal]), calib
            )[0]
            label_same = tracking_boxes_to_camera(
                TrackingAnnotation3DList([label_same]), calib
            )[0]

            dt_boxes = np.concatenate(
                [
                    result.location.reshape(1, 3),
                    result.dimensions.reshape(1, 3),
                    result.rotation_y.reshape(1, 1),
                ],
                axis=1,
            )
            dt_boxes_ideal = np.concatenate(
                [
                    result_ideal.location.reshape(1, 3),
                    result_ideal.dimensions.reshape(1, 3),
                    result_ideal.rotation_y.reshape(1, 1),
                ],
                axis=1,
            )
            dt_boxes_same = np.concatenate(
                [
                    label_same.location.reshape(1, 3) + 0.00001,
                    label_same.dimensions.reshape(1, 3) + 0.00001,
                    label_same.rotation_y.reshape(1, 1),
                ],
                axis=1,
            )
            gt_boxes = np.concatenate(
                [
                    label_lidar.location.reshape(1, 3),
                    label_lidar.dimensions.reshape(1, 3),
                    label_lidar.rotation_y.reshape(1, 1),
                ],
                axis=1,
            )
            iou3d = float(d3_box_overlap(gt_boxes, dt_boxes).astype(np.float64))
            iou3d_ideal = float(d3_box_overlap(gt_boxes, dt_boxes_ideal).astype(np.float64))
            iou3d_same = float(d3_box_overlap(gt_boxes, dt_boxes_same).astype(np.float64))

            iouAabb = iou_2d(
                result.location[:2],
                result.dimensions[:2],
                label_lidar.location[:2],
                label_lidar.dimensions[:2],
            )

            if iou3d > iou_min:
                count_tracked += 1

            accuracy = estimate_accuracy(result, label_lidar)
            accuracy_ideal = estimate_accuracy(result_ideal, label_lidar)
            accuracy_same = estimate_accuracy(label_same, label_lidar)

            ious.append((iou3d, iouAabb))
            object_precision.add_accuracy(accuracy)
            object_success.add_overlap(iou3d)
            total_precision.add_accuracy(accuracy)
            total_success.add_overlap(iou3d)
            total_precision_same.add_accuracy(accuracy_same)
            total_success_same.add_overlap(iou3d_same)
            total_precision_ideal.add_accuracy(accuracy_ideal)
            total_success_ideal.add_overlap(iou3d_ideal)

            print(
                track_id,
                "%",
                object_id,
                "[",
                i,
                "/",
                count - 1,
                "] iou3d =",
                iou3d,
                "iouAabb =",
                iouAabb,
                "accuracy(error) =",
                accuracy,
                "ve = ", object_vertical_error.get(-1),
                "ve_nr = ", object_vertical_error_no_regress.get(-1)
            )

            print()

        all_vertical_error.append(object_vertical_error.get(-1))
        all_vertical_error_no_regress.append(object_vertical_error_no_regress.get(-1))

        if len(ious) <= 0:
            mean_iou3d = None
            mean_iouAabb = None
            mean_precision = None
            mean_success = None
            tracked = None
        else:
            mean_iou3d = sum([iou3d for iou3d, iouAabb in ious]) / len(ious)
            mean_iouAabb = sum([iouAabb for iou3d, iouAabb in ious]) / len(ious)
            tracked = count_tracked / len(ious)
            mean_precision = object_precision.average
            mean_success = object_success.average

        print("mean_iou3d =", mean_iou3d)
        print("mean_iouAabb =", mean_iouAabb)
        print("tracked =", tracked)
        print("mean_precision =", mean_precision)
        print("mean_success =", mean_success)

        os.makedirs("./plots/video/" + model_name, exist_ok=True)
        os.makedirs("./plots/video/" + model_name + "/all", exist_ok=True)

        filename_y = lambda x, y: (
            "./plots/video/"
            + model_name + "/"
            + x
            + "_"
            + model_name
            + "_track_"
            + str(track_id)
            + "_obj_"
            + str(object_id)
            + "_steps_"
            + str(load)
            + y
            + ".gif"
        )

        filename = lambda x: filename_y(x, "")

        if draw:
            imageio.mimsave(filename("infer"), images)
            pygifsicle.optimize(filename("infer"))
            print("Saving", "infer", "video")

            for group, g_images in learner._images.items():
                print("Saving", group, "video")
                imageio.mimsave(filename(group), g_images)
                pygifsicle.optimize(filename(group))

                if group == "summary":
                    stacked_images = [
                        stack_images(
                            [cv2.resize(x, (0, 0), fx=5, fy=5), y], "horizontal"
                        )
                        for x, y in zip(g_images, images)
                    ]

                    for i, img in enumerate(stacked_images):
                        image = PilImage.fromarray(img)
                        image.save(filename_y("all/", "_" + str(start_frame + i)))

                    imageio.mimsave(filename("all"), stacked_images)
                    pygifsicle.optimize(filename(group))

        return mean_iou3d, mean_iouAabb, tracked, mean_precision, mean_success

    for object_id in object_ids:
        (
            mean_iou3d,
            mean_iouAabb,
            tracked,
            mean_precision,
            mean_success,
        ) = test_object_id(object_id)

        if mean_iou3d is not None:
            all_mean_iou3ds.append(mean_iou3d)
            all_mean_iouAabbs.append(mean_iouAabb)
            all_tracked.append(tracked)
            all_precision.append(mean_precision)
            all_success.append(mean_success)

    print("fps:", learner.fps())
    print("total_precision:", total_precision.average)
    print("total_success:", total_success.average)
    print("total_precision_same:", total_precision_same.average)
    print("total_success_same:", total_success_same.average)
    print("total_precision_ideal:", total_precision_ideal.average)
    print("total_success_ideal:", total_success_ideal.average)
    print("vertical_error:", vertical_error.get(-1))
    print("vertical_error_no_regress:", vertical_error_no_regress.get(-1))
    print("all_vertical_error:", all_vertical_error)
    print("all_vertical_error_no_regress:", all_vertical_error_no_regress)

    with open("./plots/video/" + model_name + "/results.txt", "a") as f:
        print("total_precision:", total_precision.average, file=f)
        print("total_success:", total_success.average, file=f)
        print("total_precision_same:", total_precision_same.average, file=f)
        print("total_success_same:", total_success_same.average, file=f)
        print("total_precision_ideal:", total_precision_ideal.average, file=f)
        print("total_success_ideal:", total_success_ideal.average, file=f)
        print("vertical_error:", vertical_error.get(-1), file=f)
        print("vertical_error_no_regress:", vertical_error_no_regress.get(-1), file=f)
        print("kwargs:", " ".join(["--" + str(k) + "=" + str(v) for k, v in kwargs.items()]), file=f)
        print("----", file=f)
        print("", file=f)

    for key, values in learner.times.items():
        t = -1 if len(values) <= 0 else (sum(values) / len(values))
        print(key, "time =", t * 1000, "ms, fps =", 1 / t)


def load_params_from_file(params_file):

    import ast
    from pathlib import Path

    path = Path(params_file)

    model_name = "none"

    parents = [p.name for p in path.parents]
    for i in range(len(parents) - 1):
        if parents[i + 1] == "temp":
            model_name = parents[i]

    int_names = [
        "load",
        "feature_blocks",
        "score_upscale",
        "rotations_count",
        "rotation_interpolation",
        "r_pos",
        "iters",
        "batch_size",
        "checkpoint_after_iter",
        "checkpoint_load_iter",
    ]

    float_names = [
        "window_influence",
        "context_amount",
        "target_feature_merge_scale",
        "rotation_penalty",
        "rotation_step",
        "lr",
        "threshold",
        "scale",
        "offset_interpolation",
        "vertical_offset_interpolation",
    ]

    object_names = [
        "optimizer_params",
        "lr_schedule_params",
        "target_size",
        "search_size",
        "overwrite_strides",
        "bof_mode",
    ]

    string_names = [
        "search_type",
        "target_type",
        "bof_mode",
        "model_config_path",
        "optimizer",
        "lr_schedule",
        "backbone",
        "network_head",
        "temp_path",
        "device",
        "tanet_config_path",
        "extrapolation_mode",
        "upscaling_mode",
    ]

    with open(params_file, "r") as f:
        str_values = f.readlines()

        params = None
        result = {}

        for s in str_values:
            key, value = s.split(" = ")
            if key == "params":
                params = value

        if params is None:
            return result

        def parse_args(name, value):
            if name in int_names:
                return int(value)
            elif name in float_names:
                return float(value)
            elif name in object_names:
                return ast.literal_eval(value)
            elif name in string_names:
                return value.replace('\n', '')[:-1]

        args = list(
            map(
                lambda x: (x[0], parse_args(x[0], x[1])),
                map(
                    lambda x: x.split("="),
                    filter(lambda x: "=" in x, params.split("--")),
                ),
            )
        )

        for name, value in args:
            result[name] = value

        result["model_name"] = model_name

        return result


def test_rotated_pp_siamese_eval(
    model_name=None,
    load=0,
    draw=False,
    iou_min=0.0,
    classes=["Car", "Van", "Truck"],
    tracks=None,
    device=DEVICE,
    eval_id="default",
    near_distance=30,
    backbone="pp",
    raise_on_infer_error=False,
    limit_object_ids=False,
    params_file=None,
    **kwargs,
):

    if params_file is not None:
        params = load_params_from_file(params_file)
        model_name = params["model_name"] if "model_name" in params else model_name
        load = params["load"] if ("load" in params and load == 0) else load
        draw = params["draw"] if "draw" in params else draw
        iou_min = params["iou_min"] if "iou_min" in params else iou_min
        classes = params["classes"] if "classes" in params else classes
        tracks = params["tracks"] if "tracks" in params else tracks
        device = params["device"] if "device" in params else device
        eval_id = params["eval_id"] if "eval_id" in params else eval_id
        near_distance = (
            params["near_distance"] if "near_distance" in params else near_distance
        )
        backbone = params["backbone"] if "backbone" in params else backbone
        raise_on_infer_error = (
            params["raise_on_infer_error"]
            if "raise_on_infer_error" in params
            else raise_on_infer_error
        )
        limit_object_ids = (
            params["limit_object_ids"]
            if "limit_object_ids" in params
            else limit_object_ids
        )

        for k, v in params.items():
            if (
                k
                not in [
                    "model_name",
                    "load",
                    "draw",
                    "iou_min",
                    "classes",
                    "tracks",
                    "device",
                    "eval_id",
                    "near_distance",
                    "backbone",
                    "raise_on_infer_error",
                    "limit_object_ids",
                ]
            ) and (k not in kwargs):
                kwargs[k] = v

    print("Eval", name, "start", file=sys.stderr)
    print("Using device:", device)
    import pygifsicle
    import imageio

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        backbone=backbone,
        checkpoint_after_iter=2000,
        **kwargs,
    )

    checkpoints_path = "./temp/" + model_name + "/checkpoints"
    results_path = "./temp/" + model_name

    if load == 0:
        learner.load(checkpoints_path, backbone=False, verbose=True)
    elif load == "pretrained":
        learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    else:
        learner.load_from_checkpoint(checkpoints_path, load)

    total_success = Success()
    total_precision = Precision()
    total_success_near = Success()
    total_precision_near = Precision()
    total_success_far = Success()
    total_precision_far = Precision()
    total_success_ideal = Success()
    total_precision_ideal = Precision()
    total_success_same = Success()
    total_precision_same = Precision()
    vertical_error = AverageMetric()
    vertical_error_no_regress = AverageMetric()
    all_vertical_error = []
    all_vertical_error_no_regress = []

    object_precisions = []
    object_sucesses = []

    def test_track(track_id):
        # count = 120
        dataset = LabeledTrackingPointCloudsDatasetIterator(
            dataset_tracking_path + "/training/velodyne/" + track_id,
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt",
            dataset_tracking_path + "/training/calib/" + track_id + ".txt",
        )
        count = len(dataset)

        all_mean_iou3ds = []
        all_mean_iouAabbs = []
        all_tracked = []
        all_precision = []
        all_success = []

        def test_object_id(object_id):

            start_frame = -1

            selected_labels = []

            object_success = Success()
            object_precision = Precision()
            object_vertical_error = AverageMetric()
            object_vertical_error_no_regress = AverageMetric()

            while len(selected_labels) <= 0:
                start_frame += 1

                if start_frame >= len(dataset):
                    return None, None, None, None, None

                point_cloud_with_calibration, labels = dataset[start_frame]
                selected_labels = TrackingAnnotation3DList(
                    [label for label in labels if (label.id == object_id)]
                )

            if not selected_labels[0].name in classes:
                return None, None, None, None, None

            total_precision.add_accuracy(0.0)
            total_success.add_overlap(1.0)

            calib = point_cloud_with_calibration.calib
            labels_lidar = tracking_boxes_to_lidar(
                selected_labels, calib, classes=classes
            )
            label_lidar = labels_lidar[0]

            learner.init(point_cloud_with_calibration, label_lidar)

            images = []
            ious = []
            count_tracked = 0

            for i in range(start_frame, count):
                point_cloud_with_calibration, labels = dataset[i]
                selected_labels = TrackingAnnotation3DList(
                    [label for label in labels if label.id == object_id]
                )

                if len(selected_labels) <= 0:
                    break

                calib = point_cloud_with_calibration.calib
                labels_lidar = tracking_boxes_to_lidar(selected_labels, calib)
                label_lidar = labels_lidar[0] if len(labels_lidar) > 0 else None

                try:

                    result = learner.infer(
                        point_cloud_with_calibration, id=-1, frame=i, draw=False,
                    )

                    all_labels = (
                        result
                        if label_lidar is None
                        else TrackingAnnotation3DList([result[0], label_lidar])
                    )
                    image = draw_point_cloud_bev(
                        point_cloud_with_calibration.data, all_labels
                    )

                    if draw:
                        pil_image = PilImage.fromarray(image)
                        images.append(pil_image)

                    result_ideal = TrackingAnnotation3D(
                        result[0].name,
                        result[0].truncated,
                        result[0].occluded,
                        result[0].alpha,
                        result[0].bbox2d,
                        result[0].dimensions,
                        np.array(
                            [*result[0].location[:-1], label_lidar.location[-1]]
                        ),
                        result[0].rotation_y,
                        result[0].id,
                        1,
                        result[0].frame,
                    )
                    label_same = TrackingAnnotation3D(
                        label_lidar.name,
                        label_lidar.truncated,
                        label_lidar.occluded,
                        label_lidar.alpha,
                        label_lidar.bbox2d,
                        label_lidar.dimensions,
                        label_lidar.location,
                        label_lidar.rotation_y,
                        label_lidar.id,
                        1,
                        label_lidar.frame,
                    )

                    iouAabb = iou_2d(
                        result[0].location[:2],
                        result[0].dimensions[:2],
                        label_lidar.location[:2],
                        label_lidar.dimensions[:2],
                    )

                    vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
                    vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))
                    object_vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
                    object_vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))

                    result = tracking_boxes_to_camera(result, calib)[0]

                    label_lidar = tracking_boxes_to_camera(
                        TrackingAnnotation3DList([label_lidar]), calib
                    )[0]
                    result_ideal = tracking_boxes_to_camera(
                        TrackingAnnotation3DList([result_ideal]), calib
                    )[0]
                    label_same = tracking_boxes_to_camera(
                        TrackingAnnotation3DList([label_same]), calib
                    )[0]

                    dt_boxes = np.concatenate(
                        [
                            result.location.reshape(1, 3),
                            result.dimensions.reshape(1, 3),
                            result.rotation_y.reshape(1, 1),
                        ],
                        axis=1,
                    )
                    gt_boxes = np.concatenate(
                        [
                            label_lidar.location.reshape(1, 3),
                            label_lidar.dimensions.reshape(1, 3),
                            label_lidar.rotation_y.reshape(1, 1),
                        ],
                        axis=1,
                    )
                    dt_boxes_ideal = np.concatenate(
                        [
                            result_ideal.location.reshape(1, 3),
                            result_ideal.dimensions.reshape(1, 3),
                            result_ideal.rotation_y.reshape(1, 1),
                        ],
                        axis=1,
                    )
                    dt_boxes_same = np.concatenate(
                        [
                            label_same.location.reshape(1, 3) + 0.00001,
                            label_same.dimensions.reshape(1, 3) + 0.00001,
                            label_same.rotation_y.reshape(1, 1),
                        ],
                        axis=1,
                    )
                    iou3d = float(d3_box_overlap(gt_boxes, dt_boxes).astype(np.float64))
                    iou3d_ideal = float(d3_box_overlap(gt_boxes, dt_boxes_ideal).astype(np.float64))
                    iou3d_same = float(d3_box_overlap(gt_boxes, dt_boxes_same).astype(np.float64))

                    if iou3d > iou_min:
                        count_tracked += 1

                    accuracy = estimate_accuracy(result, label_lidar)
                    accuracy_ideal = estimate_accuracy(result_ideal, label_lidar)
                    accuracy_same = estimate_accuracy(label_same, label_lidar)
                except Exception as e:
                    if raise_on_infer_error:
                        raise e
                    else:
                        print(e)
                        iou3d = 0
                        iouAabb = 0
                        accuracy = 0
                        iou3d_ideal = 0
                        iou3d_same = 0
                        accuracy_ideal = 0
                        accuracy_same = 0

                distance = np.linalg.norm(label_lidar.location, ord=2)
                ious.append((iou3d, iouAabb))
                object_precision.add_accuracy(accuracy)
                object_success.add_overlap(iou3d)
                total_precision.add_accuracy(accuracy)
                total_success.add_overlap(iou3d)

                total_precision_same.add_accuracy(accuracy_same)
                total_success_same.add_overlap(iou3d_same)
                total_precision_ideal.add_accuracy(accuracy_ideal)
                total_success_ideal.add_overlap(iou3d_ideal)

                if distance < near_distance:
                    total_precision_near.add_accuracy(accuracy)
                    total_success_near.add_overlap(iou3d)
                else:
                    total_precision_far.add_accuracy(accuracy)
                    total_success_far.add_overlap(iou3d)

                print(
                    track_id,
                    "%",
                    object_id,
                    "[",
                    i,
                    "/",
                    count - 1,
                    "] iou3d =",
                    iou3d,
                    "iouAabb =",
                    iouAabb,
                    "accuracy(error) =",
                    accuracy,
                    "distance =",
                    distance,
                    "ve = ", object_vertical_error.get(-1),
                    "ve_nr = ", object_vertical_error_no_regress.get(-1)
                )

            all_vertical_error.append(object_vertical_error.get(-1))
            all_vertical_error_no_regress.append(object_vertical_error_no_regress.get(-1))

            os.makedirs("./plots/video/" + model_name, exist_ok=True)

            filename = (
                "./plots/video/" + model_name + "/eval_"
                + model_name
                + "_track_"
                + str(track_id)
                + "_obj_"
                + str(object_id)
                + "_"
                + str(load)
                + "_"
                + str(eval_id)
                + ".gif"
            )

            os.makedirs(str(Path(filename).parent), exist_ok=True)

            if len(ious) <= 0:
                mean_iou3d = None
                mean_iouAabb = None
                mean_precision = None
                mean_success = None
                tracked = None
            else:
                mean_iou3d = sum([iou3d for iou3d, iouAabb in ious]) / len(ious)
                mean_iouAabb = sum([iouAabb for iou3d, iouAabb in ious]) / len(ious)
                tracked = count_tracked / len(ious)
                mean_precision = object_precision.average
                mean_success = object_success.average

            print("mean_iou3d =", mean_iou3d)
            print("mean_iouAabb =", mean_iouAabb)
            print("tracked =", tracked)
            print("mean_precision =", mean_precision)
            print("mean_success =", mean_success)

            if draw and len(images) > 0:
                imageio.mimsave(filename, images)
                pygifsicle.optimize(filename)

            return mean_iou3d, mean_iouAabb, tracked, mean_precision, mean_success

        for object_id in (
            range(0, min(5, dataset.max_id + 1))
            if limit_object_ids
            else range(0, dataset.max_id + 1)
        ):
            (
                mean_iou3d,
                mean_iouAabb,
                tracked,
                mean_precision,
                mean_success,
            ) = test_object_id(object_id)

            if mean_iou3d is not None:
                all_mean_iou3ds.append(mean_iou3d)
                all_mean_iouAabbs.append(mean_iouAabb)
                all_tracked.append(tracked)
                all_precision.append(mean_precision)
                all_success.append(mean_success)

        object_precisions.append([str(i) + ": " + str(x) for i, x in enumerate(all_precision)])
        object_sucesses.append([str(i) + ": " + str(x) for i, x in enumerate(all_success)])

        if len(all_mean_iou3ds) > 0:
            track_mean_iou3d = sum(all_mean_iou3ds) / len(all_mean_iou3ds)
            track_mean_iouAabb = sum(all_mean_iouAabbs) / len(all_mean_iouAabbs)
            track_mean_tracked = sum(all_tracked) / len(all_tracked)
            track_mean_precision = sum(all_precision) / len(all_precision)
            track_mean_success = sum(all_success) / len(all_success)
        else:
            track_mean_iou3d = None
            track_mean_iouAabb = None
            track_mean_tracked = None
            track_mean_precision = None
            track_mean_success = None

        print("track_mean_iou3d =", track_mean_iou3d)
        print("track_mean_iouAabb =", track_mean_iouAabb)
        print("track_mean_tracked =", track_mean_tracked)
        print("track_mean_precision =", track_mean_precision)
        print("track_mean_success =", track_mean_success)

        return (
            track_mean_iou3d,
            track_mean_iouAabb,
            track_mean_tracked,
            track_mean_precision,
            track_mean_success,
        )

    if tracks is None:
        tracks = [
            # "0000",
            # "0001",
            # "0002",
            # "0003",
            # "0004",
            # "0005",
            # "0006",
            # "0007",
            # "0008",
            # "0009",
            "0010",
            "0011",
            # "0012",
            # "0013",
            # "0014",
            # "0015",
            # "0016",
            # "0017",
            # "0018",
            # "0019",
            # "0020",
        ]

    all_iou3ds = []
    all_iouAabbs = []
    all_tracked = []
    all_precision = []
    all_success = []

    for track in tracks:
        (
            track_mean_iou3d,
            track_mean_iouAabb,
            track_mean_tracked,
            track_mean_precision,
            track_mean_success,
        ) = test_track(track)

        if track_mean_iou3d is not None:
            all_iou3ds.append(track_mean_iou3d)
            all_iouAabbs.append(track_mean_iouAabb)
            all_tracked.append(track_mean_tracked)
            all_precision.append(track_mean_precision)
            all_success.append(track_mean_success)

    total_mean_iou3d = sum(all_iou3ds) / len(all_iou3ds)
    total_mean_iouAabb = sum(all_iouAabbs) / len(all_iouAabbs)
    total_mean_tracked = sum(all_tracked) / len(all_tracked)
    total_mean_precision = sum(all_precision) / len(all_precision)
    total_mean_success = sum(all_success) / len(all_success)

    params = {
        "backbone": backbone,
        "load": load,
        **kwargs,
    }

    params_str = ""

    for key, value in params.items():
        params_str += "--" + key + "=" + str(value) + " "

    result = {
        "total_mean_iou3d": total_mean_iou3d,
        "total_mean_iouAabb": total_mean_iouAabb,
        "total_mean_tracked": total_mean_tracked,
        "total_mean_precision": total_mean_precision,
        "total_mean_success": total_mean_success,
        "total_precision_near": total_precision_near.average,
        "total_success_near": total_success_near.average,
        "total_precision_far": total_precision_far.average,
        "total_success_far": total_success_far.average,
        "fps": learner.fps(),
        "params": params_str,
        "object_precisions": object_precisions,
        "object_sucesses": object_sucesses,
        "total_precision_same": total_precision_same.average,
        "total_success_same": total_success_same.average,
        "total_precision_ideal": total_precision_ideal.average,
        "total_success_ideal": total_success_ideal.average,
        "total_precision": total_precision.average,
        "total_success": total_success.average,
        "vertical_error:": vertical_error.get(-1),
        "vertical_error_no_regress:": vertical_error_no_regress.get(-1),
        "all_vertical_error:": all_vertical_error,
        "all_vertical_error_no_regress:": all_vertical_error_no_regress,
    }

    for k, v in result.items():
        print(k, "=", v)

    print("all_iou3ds =", all_iou3ds)
    print("all_iouAabbs =", all_iouAabbs)
    print("all_tracked =", all_tracked)
    print("all_precision =", all_precision)
    print("all_success =", all_success)

    results_filename = results_path + "/results_" + str(load) + "_" + str(eval_id) + ".txt"
    os.makedirs(str(Path(results_filename).parent), exist_ok=True)

    with open(
        results_filename, "w"
    ) as f:

        for k, v in result.items():
            print(k, "=", v, file=f)

        print("all_iou3ds =", all_iou3ds, file=f)
        print("all_iouAabbs =", all_iouAabbs, file=f)
        print("all_tracked =", all_tracked, file=f)
        print("all_precision =", all_precision, file=f)
        print("all_success =", all_success, file=f)
        print("tracks =", tracks, file=f)

    return result


def create_extended_eval_kwargs():
    params = {
        "window_influence": [0.35, 0.45],
        "score_upscale": [8, 16],
        "rotation_penalty": [0.98, 0.96],
        "rotation_step": [0.15, 0.1, 0.075, 0.04],
        "rotations_count": [3, 5],
        "target_feature_merge_scale": [0, 0.005, 0.01],
        "search_type": [["normal", "n"], ["small", "s"], ["a+4", "4"]],
        "target_type": [["normal", "n"], ["original", "o"], ["a+1", "1"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for rotation_step in params["rotation_step"]:
                    for rotations_count in params["rotations_count"]:
                        for target_feature_merge_scale in params[
                            "target_feature_merge_scale"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    name = (
                                        str(rotations_count).replace(".", "")
                                        + "r"
                                        + str(rotation_step).replace(".", "")
                                        + "-rp"
                                        + str(rotation_penalty).replace(".", "")
                                        + "-s"
                                        + str(search_type_name).replace(".", "")
                                        + "t"
                                        + str(target_type_name).replace(".", "")
                                        + "-su"
                                        + str(score_upscale).replace(".", "")
                                        + "wi"
                                        + str(window_influence).replace(".", "")
                                        + "tfms"
                                        + str(target_feature_merge_scale).replace(".", "")
                                    )

                                    results[name] = {
                                        "window_influence": window_influence,
                                        "score_upscale": score_upscale,
                                        "rotation_penalty": rotation_penalty,
                                        "rotation_step": rotation_step,
                                        "rotations_count": rotations_count,
                                        "target_feature_merge_scale": target_feature_merge_scale,
                                        "search_type": search_type,
                                        "target_type": target_type,
                                    }
    return results


def create_small_eval_kwargs():
    params = {
        "window_influence": [0.35],
        "score_upscale": [8, 16],
        "rotation_penalty": [0.98],
        "rotation_step": [0.15, 0.1],
        "rotations_count": [3],
        "target_feature_merge_scale": [0.005, 0],
        "search_type": [["normal", "n"], ["small", "s"], ["a+4", "4"]],
        "target_type": [["normal", "n"], ["original", "o"], ["a+1", "1"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for rotation_step in params["rotation_step"]:
                    for rotations_count in params["rotations_count"]:
                        for target_feature_merge_scale in params[
                            "target_feature_merge_scale"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    name = (
                                        str(rotations_count).replace(".", "")
                                        + "r"
                                        + str(rotation_step).replace(".", "")
                                        + "-rp"
                                        + str(rotation_penalty).replace(".", "")
                                        + "-s"
                                        + str(search_type_name).replace(".", "")
                                        + "t"
                                        + str(target_type_name).replace(".", "")
                                        + "-su"
                                        + str(score_upscale).replace(".", "")
                                        + "wi"
                                        + str(window_influence).replace(".", "")
                                        + "tfms"
                                        + str(target_feature_merge_scale).replace(".", "")
                                    )

                                    results[name] = {
                                        "window_influence": window_influence,
                                        "score_upscale": score_upscale,
                                        "rotation_penalty": rotation_penalty,
                                        "rotation_step": rotation_step,
                                        "rotations_count": rotations_count,
                                        "target_feature_merge_scale": target_feature_merge_scale,
                                        "search_type": search_type,
                                        "target_type": target_type,
                                    }
    return results


def eval_all_extended(
    model_name,
    iou_min=0.0,
    loads=[2000, 4000, 8000, 16000, 32000],
    train_steps=64000,
    save_step=2000,
    tracks=None,
    device="cuda:0",
    eval_kwargs_name="extended",
    eval_id_prefix="",
    draw=False,
    **kwargs,
):

    eval_kwargs = {
        "extended": create_extended_eval_kwargs,
        "small": create_small_eval_kwargs,
        "small_val": create_small_val_eval_kwargs,
    }[eval_kwargs_name]()

    results = {}

    for load in loads:

        if abs(load) < save_step:
            load = train_steps * load

        if load == -train_steps:
            load = train_steps

        if load < 0:
            load = train_steps + load

        load = int(load)
        load = load - (load % save_step)

        for id, e_kwargs in eval_kwargs.items():

            result = test_rotated_pp_siamese_eval(
                model_name,
                load,
                draw,
                iou_min,
                tracks=tracks,
                device=device,
                eval_id=eval_id_prefix + id,
                **kwargs,
                **e_kwargs,
            )
            results[str(load) + "_" + str(id)] = result

    return results


def create_small_val_eval_kwargs():
    params = {
        "window_influence": [0.35, 0.75, 0.85, 0.95],
        "score_upscale": [8, 16],
        "rotation_penalty": [0.98],
        "rotation_step": [0.15, 0.1],
        "rotations_count": [3],
        "target_feature_merge_scale": [0.05, 0.005, 0],
        "search_type": [["normal", "n"], ["small", "s"], ["a+4", "4"]],
        "target_type": [["normal", "n"], ["original", "o"], ["a+1", "1"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for rotation_step in params["rotation_step"]:
                    for rotations_count in params["rotations_count"]:
                        for target_feature_merge_scale in params[
                            "target_feature_merge_scale"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    name = (
                                        str(rotations_count).replace(".", "")
                                        + "r"
                                        + str(rotation_step).replace(".", "")
                                        + "-rp"
                                        + str(rotation_penalty).replace(".", "")
                                        + "-s"
                                        + str(search_type_name).replace(".", "")
                                        + "t"
                                        + str(target_type_name).replace(".", "")
                                        + "-su"
                                        + str(score_upscale).replace(".", "")
                                        + "wi"
                                        + str(window_influence).replace(".", "")
                                        + "tfms"
                                        + str(target_feature_merge_scale).replace(".", "")
                                    )

                                    results[name] = {
                                        "window_influence": window_influence,
                                        "score_upscale": score_upscale,
                                        "rotation_penalty": rotation_penalty,
                                        "rotation_step": rotation_step,
                                        "rotations_count": rotations_count,
                                        "target_feature_merge_scale": target_feature_merge_scale,
                                        "search_type": search_type,
                                        "target_type": target_type,
                                    }
    return results


def eval_all_extended_val_set(
    model_name,
    iou_min=0.0,
    loads=[2000, 4000, 8000, 16000, 32000],
    train_steps=64000,
    save_step=2000,
    device="cuda:0",
    eval_kwargs_name="small",
    **kwargs,
):
    tracks = None
    return eval_all_extended(
        model_name,
        iou_min,
        loads,
        train_steps,
        save_step,
        tracks,
        device,
        eval_kwargs_name,
        eval_id_prefix="val_set_",
        **kwargs,
    )


def eval_all_extended_another_val_set(
    model_name,
    iou_min=0.0,
    loads=[2000, 4000, 8000, 16000, 32000],
    train_steps=64000,
    save_step=2000,
    device="cuda:0",
    eval_kwargs_name="small_val",
    tracks=["0010", "0011"],
    **kwargs,
):

    return eval_all_extended(
        model_name,
        iou_min,
        loads,
        train_steps,
        save_step,
        tracks,
        device,
        eval_kwargs_name,
        eval_id_prefix="another_val_set_",
        **kwargs,
    )


def eval_all_extended_test_set(
    model_name,
    iou_min=0.0,
    loads=[2000, 4000, 8000, 16000, 32000],
    train_steps=64000,
    save_step=2000,
    device="cuda:0",
    eval_kwargs_name="small",
    **kwargs,
):
    tracks = ["0019", "0020"]
    return eval_all_extended(
        model_name,
        iou_min,
        loads,
        train_steps,
        save_step,
        tracks,
        device,
        eval_kwargs_name,
        eval_id_prefix="test_set_",
        **kwargs,
    )


def create_v1_eval_kwargs():
    params = {
        "window_influence": [0.35, 0.45, 0.65, 0.85],
        "score_upscale": [8, 16, 32],
        "rotation_penalty": [0.98, 0.90],
        "offset_interpolation": [1, 0.5, 0.75, 0.3],
        "target_feature_merge_scale": [0, 0.005, 0.01],
        "extrapolation_mode": [["none", "n"], ["linear", "l"]],
        "search_type": [["normal", "n"], ["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"], ["original", "o"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for search_type, search_type_name in params[
                            "search_type"
                        ]:
                            for target_type, target_type_name in params[
                                "target_type"
                            ]:
                                for extrapolation_mode, extrapolation_mode_name in params[
                                    "extrapolation_mode"
                                ]:
                                    name = (
                                        "rp"
                                        + str(rotation_penalty).replace(".", "")
                                        + "-s"
                                        + str(search_type_name).replace(".", "")
                                        + "t"
                                        + str(target_type_name).replace(".", "")
                                        + "-su"
                                        + str(score_upscale).replace(".", "")
                                        + "-wi"
                                        + str(window_influence).replace(".", "")
                                        + "-tfms"
                                        + str(target_feature_merge_scale).replace(".", "")
                                        + "-oi"
                                        + str(offset_interpolation).replace(".", "")
                                        + "-ex"
                                        + str(extrapolation_mode_name).replace(".", "")
                                    )

                                    results[name] = {
                                        "window_influence": window_influence,
                                        "score_upscale": score_upscale,
                                        "rotation_penalty": rotation_penalty,
                                        "target_feature_merge_scale": target_feature_merge_scale,
                                        "offset_interpolation": offset_interpolation,
                                        "extrapolation_mode": extrapolation_mode,
                                        "search_type": search_type,
                                        "target_type": target_type,
                                    }
    return results


def create_v2_eval_kwargs():
    params = {
        "window_influence": [0.35, 0.85],
        "score_upscale": [8],
        "rotation_penalty": [0.98, 0.90],
        "offset_interpolation": [1, 0.5, 0.75, 0.3],
        "target_feature_merge_scale": [0, 0.005, 0.01],
        "min_top_score": [1.0, 0.4],
        "extrapolation_mode": [["none", "n"], ["linear", "l"]],
        "search_type": [["normal", "n"], ["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"], ["original", "o"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def create_v3_eval_kwargs():
    params = {
        "window_influence": [0.45, 0.85],
        "score_upscale": [16, 8, 32, 1],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.35, 0.25],
        "target_feature_merge_scale": [0],
        "min_top_score": [None, 1.0, 0.4],
        "extrapolation_mode": [["linear", "l"]],
        "search_type": [["normal", "n"], ["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def create_v4_eval_kwargs():
    params = {
        "window_influence": [0.85, 0.82, 0.87],
        "score_upscale": [16, 8],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.27, 0.25],
        "target_feature_merge_scale": [0],
        "min_top_score": [None, 0.4],
        "extrapolation_mode": [["linear", "l"]],
        "search_type": [["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def create_v5_eval_kwargs():
    params = {
        "window_influence": [0.85, 0.82, 0.80],
        "score_upscale": [16, 8],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.31, 0.32, 0.33, 0.34, 0.35, 0.29, 0.28, 0.27],
        "target_feature_merge_scale": [0],
        "min_top_score": [None, 0.4],
        "extrapolation_mode": [["linear", "l"]],
        "search_type": [["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def create_v6_eval_kwargs():
    params = {
        "window_influence": [0.85, 0.86, 0.85, 0.84, 0.87],
        "score_upscale": [16, 8],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.25, 0.255, 0.245, 0.305, 0.295, 0.253, 0.247],
        "target_feature_merge_scale": [0, 0.01, 0.005],
        "min_top_score": [None, 0.1],
        "extrapolation_mode": [["linear", "l"]],
        "search_type": [["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"], ["original", "o"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def create_v6c_eval_kwargs():
    params = {
        "window_influence": [0.85, 0.8, 0.9],
        "score_upscale": [16, 8],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.25, 0.255, 0.245, 0.305, 0.295, 0.253, 0.247],
        "target_feature_merge_scale": [0, 0.01, 0.005],
        "min_top_score": [None, 0.1],
        "extrapolation_mode": [["linear", "l"]],
        "search_type": [["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"], ["original", "o"]],
        "context_amount": [0.23, 0.24, 0.25, 0.26, 0.27, 0.28, 0.29, 0.30, 0.31, 0.32],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        for context_amount in params[
                                            "context_amount"
                                        ]:
                                            name = (
                                                "rp"
                                                + str(rotation_penalty).replace(".", "")
                                                + "-s"
                                                + str(search_type_name).replace(".", "")
                                                + "t"
                                                + str(target_type_name).replace(".", "")
                                                + "-su"
                                                + str(score_upscale).replace(".", "")
                                                + "-wi"
                                                + str(window_influence).replace(".", "")
                                                + "-tfms"
                                                + str(target_feature_merge_scale).replace(".", "")
                                                + "-mts"
                                                + str(min_top_score).replace(".", "")
                                                + "-oi"
                                                + str(offset_interpolation).replace(".", "")
                                                + "-ex"
                                                + str(extrapolation_mode_name).replace(".", "")
                                                + "-c"
                                                + str(context_amount).replace(".", "")
                                            )

                                            results[name] = {
                                                "window_influence": window_influence,
                                                "score_upscale": score_upscale,
                                                "rotation_penalty": rotation_penalty,
                                                "target_feature_merge_scale": target_feature_merge_scale,
                                                "min_top_score": min_top_score,
                                                "offset_interpolation": offset_interpolation,
                                                "extrapolation_mode": extrapolation_mode,
                                                "search_type": search_type,
                                                "target_type": target_type,
                                                "context_amount": context_amount,
                                            }
    return results


def create_av3_eval_kwargs():
    params = {
        "window_influence": [0.45, 0.85],
        "score_upscale": [16, 8, 32, 1],
        "rotation_penalty": [0.98],
        "offset_interpolation": [0.3, 0.35, 0.25],
        "target_feature_merge_scale": [0],
        "min_top_score": [None, 1.0, 0.4],
        "extrapolation_mode": [["linear+", "lp"], ["linear", "l"]],
        "search_type": [["normal", "n"], ["small", "s"], ["snormal", "sn"]],
        "target_type": [["normal", "n"]],
    }
    results = {}

    for window_influence in params["window_influence"]:
        for score_upscale in params["score_upscale"]:
            for rotation_penalty in params["rotation_penalty"]:
                for offset_interpolation in params["offset_interpolation"]:
                    for target_feature_merge_scale in params[
                        "target_feature_merge_scale"
                    ]:
                        for min_top_score in params[
                            "min_top_score"
                        ]:
                            for search_type, search_type_name in params[
                                "search_type"
                            ]:
                                for target_type, target_type_name in params[
                                    "target_type"
                                ]:
                                    for extrapolation_mode, extrapolation_mode_name in params[
                                        "extrapolation_mode"
                                    ]:
                                        name = (
                                            "rp"
                                            + str(rotation_penalty).replace(".", "")
                                            + "-s"
                                            + str(search_type_name).replace(".", "")
                                            + "t"
                                            + str(target_type_name).replace(".", "")
                                            + "-su"
                                            + str(score_upscale).replace(".", "")
                                            + "-wi"
                                            + str(window_influence).replace(".", "")
                                            + "-tfms"
                                            + str(target_feature_merge_scale).replace(".", "")
                                            + "-mts"
                                            + str(min_top_score).replace(".", "")
                                            + "-oi"
                                            + str(offset_interpolation).replace(".", "")
                                            + "-ex"
                                            + str(extrapolation_mode_name).replace(".", "")
                                        )

                                        results[name] = {
                                            "window_influence": window_influence,
                                            "score_upscale": score_upscale,
                                            "rotation_penalty": rotation_penalty,
                                            "target_feature_merge_scale": target_feature_merge_scale,
                                            "min_top_score": min_top_score,
                                            "offset_interpolation": offset_interpolation,
                                            "extrapolation_mode": extrapolation_mode,
                                            "search_type": search_type,
                                            "target_type": target_type,
                                        }
    return results


def multi_eval(
    id=0,
    gpu_capacity=4,
    total_devices=4,
    model_name=None,
    tracks=["0010", "0011"],
    eval_kwargs_name="av3",
    params_file=None,
    eval_id_prefix="",
    draw=False,
    **kwargs,
):

    eval_kwargs = {
        "v1": create_v1_eval_kwargs,
        "v2": create_v2_eval_kwargs,
        "v3": create_v3_eval_kwargs,
        "v4": create_v4_eval_kwargs,
        "v5": create_v5_eval_kwargs,
        "v6": create_v6_eval_kwargs,
        "v6c": create_v6c_eval_kwargs,
        "av3": create_av3_eval_kwargs,
    }[eval_kwargs_name]()

    results = {}

    runs = [(id, e_kwargs) for (id, e_kwargs) in eval_kwargs.items()]

    device_id = id % total_devices
    i = id

    print("id =", id, "runs per process = ", len(runs) / (gpu_capacity * total_devices))

    while i < len(runs):

        id, e_kwargs = runs[i]

        if params_file is None:
            eval_id = "multi-" + eval_kwargs_name + eval_id_prefix + "/" + id
        else:
            eval_id = (
                "multi-" + eval_kwargs_name +
                params_file.replace("./", "").replace("temp/", "").replace("/", "").replace(".txt", "") +
                eval_id_prefix + "/" + id
            )

        result = test_rotated_pp_siamese_eval(
            model_name,
            draw=draw,
            tracks=tracks,
            device="cuda:" + str(device_id),
            eval_id=eval_id,
            params_file=params_file,
            **kwargs,
            **e_kwargs,
        )
        results[str(id)] = result

        i += gpu_capacity * total_devices

    return results


def test_realtime_rotated_pp_siamese_eval(
    model_name=None,
    load=0,
    draw=False,
    iou_min=0.0,
    classes=["Car", "Van", "Truck"],
    tracks=None,
    device=DEVICE,
    eval_id="default",
    near_distance=30,
    backbone="pp",
    raise_on_infer_error=False,
    limit_object_ids=False,
    params_file=None,
    data_fps=20,
    require_predictive_inference=False,
    wait_for_next_frame=False,
    cap_model_fps=None,
    warmups_needed=10,
    **kwargs,
):

    if params_file is not None:
        params = load_params_from_file(params_file)
        model_name = params["model_name"] if "model_name" in params else model_name
        load = params["load"] if ("load" in params and load == 0) else load
        draw = params["draw"] if "draw" in params else draw
        iou_min = params["iou_min"] if "iou_min" in params else iou_min
        classes = params["classes"] if "classes" in params else classes
        tracks = params["tracks"] if "tracks" in params else tracks
        device = params["device"] if "device" in params else device
        eval_id = params["eval_id"] if "eval_id" in params else eval_id
        near_distance = (
            params["near_distance"] if "near_distance" in params else near_distance
        )
        backbone = params["backbone"] if "backbone" in params else backbone
        raise_on_infer_error = (
            params["raise_on_infer_error"]
            if "raise_on_infer_error" in params
            else raise_on_infer_error
        )
        limit_object_ids = (
            params["limit_object_ids"]
            if "limit_object_ids" in params
            else limit_object_ids
        )

        for k, v in params.items():
            if (
                k
                not in [
                    "model_name",
                    "load",
                    "draw",
                    "iou_min",
                    "classes",
                    "tracks",
                    "device",
                    "eval_id",
                    "near_distance",
                    "backbone",
                    "raise_on_infer_error",
                    "limit_object_ids",
                ]
            ) and (k not in kwargs):
                kwargs[k] = v

    print("Eval", name, "start", file=sys.stderr)
    print("Using device:", device)
    import pygifsicle
    import imageio

    learner = VpitObjectTracking3DLearner(
        model_config_path=backbone_configs[backbone],
        device=device,
        backbone=backbone,
        checkpoint_after_iter=2000,
        **kwargs,
    )

    real_time_evaluator = RealTimeEvaluator(
        data_fps=data_fps,
        require_predictive_inference=require_predictive_inference,
        wait_for_next_frame=wait_for_next_frame,
        cap_model_fps=cap_model_fps,
    )

    checkpoints_path = "./temp/" + model_name + "/checkpoints"
    results_path = "./temp/" + model_name

    if load == 0:
        learner.load(checkpoints_path, backbone=False, verbose=True)
    elif load == "pretrained":
        learner.load(backbone_model_paths[backbone], backbone=True, verbose=True)
    else:
        learner.load_from_checkpoint(checkpoints_path, load)

    total_success = Success()
    total_precision = Precision()
    total_success_near = Success()
    total_precision_near = Precision()
    total_success_far = Success()
    total_precision_far = Precision()
    total_success_ideal = Success()
    total_precision_ideal = Precision()
    total_success_same = Success()
    total_precision_same = Precision()
    vertical_error = AverageMetric()
    vertical_error_no_regress = AverageMetric()
    all_vertical_error = []
    all_vertical_error_no_regress = []

    object_precisions = []
    object_sucesses = []

    total_frames = 0
    dropped_frames = 0

    def test_track(track_id):
        # count = 120
        dataset = LabeledTrackingPointCloudsDatasetIterator(
            dataset_tracking_path + "/training/velodyne/" + track_id,
            dataset_tracking_path + "/training/label_02/" + track_id + ".txt",
            dataset_tracking_path + "/training/calib/" + track_id + ".txt",
        )
        count = len(dataset)

        all_mean_iou3ds = []
        all_mean_iouAabbs = []
        all_tracked = []
        all_precision = []
        all_success = []

        def test_object_id(object_id):

            nonlocal warmups_needed
            nonlocal total_frames
            nonlocal dropped_frames

            start_frame = -1

            selected_labels = []

            object_success = Success()
            object_precision = Precision()
            object_vertical_error = AverageMetric()
            object_vertical_error_no_regress = AverageMetric()
            point_cloud_with_calibration = None

            while len(selected_labels) <= 0:
                start_frame += 1

                if start_frame >= len(dataset):
                    return None, None, None, None, None

                point_cloud_with_calibration, labels = dataset[start_frame]
                selected_labels = TrackingAnnotation3DList(
                    [label for label in labels if (label.id == object_id)]
                )

            if not selected_labels[0].name in classes:
                return None, None, None, None, None

            calib = point_cloud_with_calibration.calib
            labels_lidar = tracking_boxes_to_lidar(
                selected_labels, calib, classes=classes
            )
            label_lidar = labels_lidar[0]

            learner.init(point_cloud_with_calibration, label_lidar)
            real_time_evaluator.init(label_lidar, labels_lidar)

            images = []
            ious = []
            count_tracked = 0

            allow_extra_last_frame = not require_predictive_inference
            extra_last_frame_used = False

            i = start_frame - 1
            while i < count:
                i += 1

                has_label = i < count
                selected_labels = []

                if has_label:
                    point_cloud_with_calibration, labels = dataset[i]
                    selected_labels = TrackingAnnotation3DList(
                        [label for label in labels if label.id == object_id]
                    )

                if len(selected_labels) <= 0:
                    if allow_extra_last_frame and not extra_last_frame_used:
                        i -= 2
                        extra_last_frame_used = True
                        continue
                    else:
                        break

                calib = point_cloud_with_calibration.calib
                labels_lidar = tracking_boxes_to_lidar(selected_labels, calib)
                label_lidar_dataset = labels_lidar[0] if len(labels_lidar) > 0 else None

                while warmups_needed > 0:
                    warmups_needed -= 1
                    print(f"Warm up [{warmups_needed}]")
                    learner.infer(
                        point_cloud_with_calibration, id=-1, frame=i, draw=False,
                    )

                label_lidar, result, frame_to_compare, frame_result = real_time_evaluator.on_data(label_lidar_dataset, i)
                print("frame_to_compare =", frame_to_compare, "frame_result =", frame_result, "frame =", i)

                if real_time_evaluator.can_frame_be_processed():

                    t0 = time.time()

                    result_infer = learner.infer(
                        point_cloud_with_calibration, id=-1, frame=i, draw=False,
                    )

                    t0 = time.time() - t0
                    real_time_evaluator.on_prediction(result_infer, t0, i)
                    total_frames += 1
                else:
                    dropped_frames += 1

                all_labels = (
                    result
                    if label_lidar is None
                    else TrackingAnnotation3DList([result[0], label_lidar])
                )
                image = draw_point_cloud_bev(
                    point_cloud_with_calibration.data, all_labels
                )

                if draw:
                    pil_image = PilImage.fromarray(image)
                    images.append(pil_image)

                result_ideal = TrackingAnnotation3D(
                    result[0].name,
                    result[0].truncated,
                    result[0].occluded,
                    result[0].alpha,
                    result[0].bbox2d,
                    result[0].dimensions,
                    np.array(
                        [*result[0].location[:-1], label_lidar.location[-1]]
                    ),
                    result[0].rotation_y,
                    result[0].id,
                    1,
                    result[0].frame,
                )
                label_same = TrackingAnnotation3D(
                    label_lidar.name,
                    label_lidar.truncated,
                    label_lidar.occluded,
                    label_lidar.alpha,
                    label_lidar.bbox2d,
                    label_lidar.dimensions,
                    label_lidar.location,
                    label_lidar.rotation_y,
                    label_lidar.id,
                    1,
                    label_lidar.frame,
                )

                iouAabb = iou_2d(
                    result[0].location[:2],
                    result[0].dimensions[:2],
                    label_lidar.location[:2],
                    label_lidar.dimensions[:2],
                )

                vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
                vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))
                object_vertical_error.update(np.abs(label_lidar.location[-1] - result[0].location[-1]))
                object_vertical_error_no_regress.update(np.abs(label_lidar.location[-1] - learner.init_label.location[-1]))

                result = tracking_boxes_to_camera(result, calib)[0]

                label_lidar = tracking_boxes_to_camera(
                    TrackingAnnotation3DList([label_lidar]), calib
                )[0]
                result_ideal = tracking_boxes_to_camera(
                    TrackingAnnotation3DList([result_ideal]), calib
                )[0]
                label_same = tracking_boxes_to_camera(
                    TrackingAnnotation3DList([label_same]), calib
                )[0]

                dt_boxes = np.concatenate(
                    [
                        result.location.reshape(1, 3),
                        result.dimensions.reshape(1, 3),
                        result.rotation_y.reshape(1, 1),
                    ],
                    axis=1,
                )
                gt_boxes = np.concatenate(
                    [
                        label_lidar.location.reshape(1, 3),
                        label_lidar.dimensions.reshape(1, 3),
                        label_lidar.rotation_y.reshape(1, 1),
                    ],
                    axis=1,
                )
                dt_boxes_ideal = np.concatenate(
                    [
                        result_ideal.location.reshape(1, 3),
                        result_ideal.dimensions.reshape(1, 3),
                        result_ideal.rotation_y.reshape(1, 1),
                    ],
                    axis=1,
                )
                dt_boxes_same = np.concatenate(
                    [
                        label_same.location.reshape(1, 3) + 0.00001,
                        label_same.dimensions.reshape(1, 3) + 0.00001,
                        label_same.rotation_y.reshape(1, 1),
                    ],
                    axis=1,
                )
                iou3d = float(d3_box_overlap(gt_boxes, dt_boxes).astype(np.float64))
                iou3d_ideal = float(d3_box_overlap(gt_boxes, dt_boxes_ideal).astype(np.float64))
                iou3d_same = float(d3_box_overlap(gt_boxes, dt_boxes_same).astype(np.float64))

                if np.all(gt_boxes == dt_boxes):
                    iou3d = 1.0

                if iou3d > iou_min:
                    count_tracked += 1

                accuracy = estimate_accuracy(result, label_lidar)
                accuracy_ideal = estimate_accuracy(result_ideal, label_lidar)
                accuracy_same = estimate_accuracy(label_same, label_lidar)

                distance = np.linalg.norm(label_lidar.location, ord=2)
                ious.append((iou3d, iouAabb))
                object_precision.add_accuracy(accuracy)
                object_success.add_overlap(iou3d)
                total_precision.add_accuracy(accuracy)
                total_success.add_overlap(iou3d)

                total_precision_same.add_accuracy(accuracy_same)
                total_success_same.add_overlap(iou3d_same)
                total_precision_ideal.add_accuracy(accuracy_ideal)
                total_success_ideal.add_overlap(iou3d_ideal)

                if distance < near_distance:
                    total_precision_near.add_accuracy(accuracy)
                    total_success_near.add_overlap(iou3d)
                else:
                    total_precision_far.add_accuracy(accuracy)
                    total_success_far.add_overlap(iou3d)

                print(
                    track_id,
                    "%",
                    object_id,
                    "[",
                    i,
                    "/",
                    count - 1,
                    "] iou3d =",
                    iou3d,
                    "iouAabb =",
                    iouAabb,
                    "accuracy(error) =",
                    accuracy,
                    "distance =",
                    distance,
                    "ve = ", object_vertical_error.get(-1),
                    "ve_nr = ", object_vertical_error_no_regress.get(-1)
                )

            all_vertical_error.append(object_vertical_error.get(-1))
            all_vertical_error_no_regress.append(object_vertical_error_no_regress.get(-1))

            os.makedirs("./plots/video/" + model_name, exist_ok=True)

            filename = (
                "./plots/video/" + model_name + "/eval_"
                + model_name
                + "_track_"
                + str(track_id)
                + "_obj_"
                + str(object_id)
                + "_"
                + str(load)
                + "_"
                + str(eval_id)
                + ".gif"
            )

            os.makedirs(str(Path(filename).parent), exist_ok=True)

            if len(ious) <= 0:
                mean_iou3d = None
                mean_iouAabb = None
                mean_precision = None
                mean_success = None
                tracked = None
            else:
                mean_iou3d = sum([iou3d for iou3d, iouAabb in ious]) / len(ious)
                mean_iouAabb = sum([iouAabb for iou3d, iouAabb in ious]) / len(ious)
                tracked = count_tracked / len(ious)
                mean_precision = object_precision.average
                mean_success = object_success.average

            print("mean_iou3d =", mean_iou3d)
            print("mean_iouAabb =", mean_iouAabb)
            print("tracked =", tracked)
            print("mean_precision =", mean_precision)
            print("mean_success =", mean_success)
            print("dropped_frames =", dropped_frames)
            print("total_frames =", total_frames)

            if draw and len(images) > 0:
                imageio.mimsave(filename, images)
                pygifsicle.optimize(filename)

            return mean_iou3d, mean_iouAabb, tracked, mean_precision, mean_success

        for object_id in (
            range(0, min(5, dataset.max_id + 1))
            if limit_object_ids
            else range(0, dataset.max_id + 1)
        ):
            (
                mean_iou3d,
                mean_iouAabb,
                tracked,
                mean_precision,
                mean_success,
            ) = test_object_id(object_id)

            if mean_iou3d is not None:
                all_mean_iou3ds.append(mean_iou3d)
                all_mean_iouAabbs.append(mean_iouAabb)
                all_tracked.append(tracked)
                all_precision.append(mean_precision)
                all_success.append(mean_success)

        object_precisions.append([str(i) + ": " + str(x) for i, x in enumerate(all_precision)])
        object_sucesses.append([str(i) + ": " + str(x) for i, x in enumerate(all_success)])

        if len(all_mean_iou3ds) > 0:
            track_mean_iou3d = sum(all_mean_iou3ds) / len(all_mean_iou3ds)
            track_mean_iouAabb = sum(all_mean_iouAabbs) / len(all_mean_iouAabbs)
            track_mean_tracked = sum(all_tracked) / len(all_tracked)
            track_mean_precision = sum(all_precision) / len(all_precision)
            track_mean_success = sum(all_success) / len(all_success)
        else:
            track_mean_iou3d = None
            track_mean_iouAabb = None
            track_mean_tracked = None
            track_mean_precision = None
            track_mean_success = None

        print("track_mean_iou3d =", track_mean_iou3d)
        print("track_mean_iouAabb =", track_mean_iouAabb)
        print("track_mean_tracked =", track_mean_tracked)
        print("track_mean_precision =", track_mean_precision)
        print("track_mean_success =", track_mean_success)

        return (
            track_mean_iou3d,
            track_mean_iouAabb,
            track_mean_tracked,
            track_mean_precision,
            track_mean_success,
        )

    if tracks is None:
        tracks = [
            # "0000",
            # "0001",
            # "0002",
            # "0003",
            # "0004",
            # "0005",
            # "0006",
            # "0007",
            # "0008",
            # "0009",
            "0010",
            "0011",
            # "0012",
            # "0013",
            # "0014",
            # "0015",
            # "0016",
            # "0017",
            # "0018",
            # "0019",
            # "0020",
        ]

    all_iou3ds = []
    all_iouAabbs = []
    all_tracked = []
    all_precision = []
    all_success = []

    for track in tracks:
        (
            track_mean_iou3d,
            track_mean_iouAabb,
            track_mean_tracked,
            track_mean_precision,
            track_mean_success,
        ) = test_track(track)

        if track_mean_iou3d is not None:
            all_iou3ds.append(track_mean_iou3d)
            all_iouAabbs.append(track_mean_iouAabb)
            all_tracked.append(track_mean_tracked)
            all_precision.append(track_mean_precision)
            all_success.append(track_mean_success)

    total_mean_iou3d = sum(all_iou3ds) / len(all_iou3ds)
    total_mean_iouAabb = sum(all_iouAabbs) / len(all_iouAabbs)
    total_mean_tracked = sum(all_tracked) / len(all_tracked)
    total_mean_precision = sum(all_precision) / len(all_precision)
    total_mean_success = sum(all_success) / len(all_success)

    params = {
        "backbone": backbone,
        "load": load,
        **kwargs,
    }

    params_str = ""

    for key, value in params.items():
        params_str += "--" + key + "=" + str(value) + " "

    result = {
        "total_mean_iou3d": total_mean_iou3d,
        "total_mean_iouAabb": total_mean_iouAabb,
        "total_mean_tracked": total_mean_tracked,
        "total_mean_precision": total_mean_precision,
        "total_mean_success": total_mean_success,
        "total_precision_near": total_precision_near.average,
        "total_success_near": total_success_near.average,
        "total_precision_far": total_precision_far.average,
        "total_success_far": total_success_far.average,
        "fps": learner.fps(),
        "params": params_str,
        "object_precisions": object_precisions,
        "object_sucesses": object_sucesses,
        "total_precision_same": total_precision_same.average,
        "total_success_same": total_success_same.average,
        "total_precision_ideal": total_precision_ideal.average,
        "total_success_ideal": total_success_ideal.average,
        "total_precision": total_precision.average,
        "total_success": total_success.average,
        "vertical_error:": vertical_error.get(-1),
        "vertical_error_no_regress:": vertical_error_no_regress.get(-1),
        "all_vertical_error:": all_vertical_error,
        "all_vertical_error_no_regress:": all_vertical_error_no_regress,
        "total_frames:": total_frames,
        "dropped_frames:": dropped_frames,
    }

    for k, v in result.items():
        print(k, "=", v)

    print("all_iou3ds =", all_iou3ds)
    print("all_iouAabbs =", all_iouAabbs)
    print("all_tracked =", all_tracked)
    print("all_precision =", all_precision)
    print("all_success =", all_success)

    results_filename = results_path + "/results_" + str(load) + "_" + str(eval_id) + ".txt"
    os.makedirs(str(Path(results_filename).parent), exist_ok=True)

    with open(
        results_filename, "w"
    ) as f:

        for k, v in result.items():
            print(k, "=", v, file=f)

        print("all_iou3ds =", all_iou3ds, file=f)
        print("all_iouAabbs =", all_iouAabbs, file=f)
        print("all_tracked =", all_tracked, file=f)
        print("all_precision =", all_precision, file=f)
        print("all_success =", all_success, file=f)
        print("tracks =", tracks, file=f)

    return result


if __name__ == "__main__":

    fire.Fire()
